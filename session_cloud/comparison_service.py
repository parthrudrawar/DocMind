"""Bounded original pairwise fact-checking and collection coverage analysis."""
from datetime import datetime, timezone
from itertools import combinations
import json
import math

from app.reasoning.citations import citation_numbers
from session_cloud.original_comparisons import contradiction_messages, gap_messages
from session_cloud.exploration_results import jobs_for, parse_exploration, finish_exploration
from session_cloud.runtime import SessionError


def parse_items(text, kind, pair=None):
    if citation_numbers(text):
        raise SessionError("analysis_invalid")
    raw = text.strip()
    if raw.startswith("```"):
        # Same original fenced JSON acceptance, but no trailing hidden output.
        if not raw.endswith("```") or raw.count("```") != 2:
            raise SessionError("analysis_invalid")
        raw = raw[3:-3]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        raise SessionError("analysis_invalid") from None
    if not isinstance(items, list) or len(items) > 20:
        raise SessionError("analysis_invalid")
    result = []
    for item in items:
        fields = {"topic": 200, **({"text_a": 1200, "text_b": 1200, "explanation": 1600}
                                  if kind == "contradictions" else {"description": 2400})}
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str)
                or not 1 <= len(item[k].strip()) <= limit for k, limit in fields.items()):
            raise SessionError("analysis_invalid")
        clean = {k: item[k] for k in fields}
        if any(citation_numbers(value) for value in clean.values()):
            # JSON Unicode escapes must not hide fabricated reference markers.
            raise SessionError("analysis_invalid")
        if kind == "contradictions":
            confidence = item.get("confidence", 0)
            if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise SessionError("analysis_invalid")
            if confidence < 0.4:  # ORIGINAL persistence noise threshold.
                continue
            a, b = pair  # Provider-supplied IDs/filenames are never authoritative.
            clean.update(doc_id_1=a["id"], doc_id_2=b["id"], doc_a=a["filename"], doc_b=b["filename"],
                         confidence=confidence)
            for suffix, doc in (("a", a), ("b", b)):
                clean["text_" + suffix + "_is_exact_excerpt"] = any(
                    item["text_" + suffix] in s["exact_quote"] for s in doc["sources"])
        result.append(clean)
    return result


def execute_collection(service, capability, ref, kind, request_id, lease, state,
                       cancel, publish, started, active, verify_inputs):
    store = service.store
    with store._operation(capability, ref):
        active(True)
        with store._lock, store._db(lease) as db:
            verify_inputs(db)
        publish({"type": "stage", "stage": "evidence", "status": "active"})
        evidence = store._comparison_inputs(capability, ref, lease, state["documents"], kind, active)
        with store._lock, store._db(lease) as db:
            verify_inputs(db)
        # Missing/corrupt evidence is NOT a successful 'no contradictions/gaps'.
        if (len(evidence["documents"]) != len(state["documents"])
                or kind == "contradictions" and len(evidence["documents"]) < 2):
            raise SessionError("analysis_evidence")
        docs = evidence["documents"]
        inputs = [dict(s, number=i) for i, s in enumerate((s for d in docs for s in d["sources"]), 1)]
        publish({"type": "stage", "stage": "evidence", "status": "complete", "selected_passages": len(inputs)})
        exploration = kind in {"questions", "insights", "timeline"}
        jobs = (jobs_for(kind, docs, state["target"]) if exploration else
                [(contradiction_messages(a, b), (a, b), 0, 1024) for a, b in combinations(docs, 2)] if kind == "contradictions" else
                [(gap_messages(docs), None, 0.3, 1024)])
        if len(jobs) > service.limits.calls_per_turn:
            raise SessionError("generation_budget")
        results, usages, reservations = [], [], []
        created = datetime.fromtimestamp(store.clock(), timezone.utc).isoformat()
        for index, (messages, pair, temperature, maximum) in enumerate(jobs, 1):
            active(True)
            reserved = service.budget.reserve(lease.owner, messages, maximum)
            reservations.append(reserved)
            publish({"type": "stage", "stage": "generating", "status": "active", "comparison": index,
                     "comparisons": len(jobs)})
            pieces, size, completed, usage = [], 0, False, None
            stream = service.provider.events(messages, maximum, active, temperature=temperature,
                seconds=min(service.limits.provider_seconds, max(1, service.limits.seconds - (service.monotonic() - started))))
            try:
                for event in stream:
                    active()
                    if event["type"] == "token":
                        piece = event["content"]
                        if not isinstance(piece, str):
                            raise SessionError("analysis_invalid")
                        size += len(piece)
                        if size > 8000:
                            raise SessionError("analysis_invalid")
                        pieces.append(piece)
                    elif event["type"] == "usage":
                        usage, completed = event["usage"], True
            finally:
                stream.close()
            active(True)
            if not completed or not pieces:
                raise SessionError("generation_incomplete")
            if usage and (usage["total_tokens"] > reserved or usage["completion_tokens"] > maximum):
                raise SessionError("analysis_invalid")
            usages.append(usage)
            results.extend(parse_exploration("".join(pieces), kind, pair) if exploration else parse_items("".join(pieces), kind, pair))
        if exploration:
            results = finish_exploration(results, kind)
        if kind == "contradictions":
            results.sort(key=lambda item: item["confidence"], reverse=True)  # ORIGINAL GET ordering.
        for i, item in enumerate(results, 1):
            item.update(id=f"{request_id}-{i}", created_at=created)
        result = {"kind": kind, "items": results, "request_id": request_id, "created_at": created,
            "document_ids": [d["id"] for d in docs], "input_sources": inputs, "provenance_only": True,
            "assessment": {"contradictions": "potential_contradictions", "gaps": "potential_knowledge_gaps",
                           "questions": "suggested_questions", "insights": "potential_insights", "timeline": "model_extracted_timeline"}[kind],
            "metrics": {"wall_seconds": service.monotonic() - started, "provider_calls": len(jobs),
                "reserved_tokens_upper_bound": sum(reservations), "provider_usage": usages,
                "evidence": evidence["metrics"]}}
        if kind in {"questions", "insights"}:
            result["topic"] = state["target"]
        payload = json.dumps(result)
        if len(payload.encode()) > 24_000:
            raise SessionError("analysis_invalid")
        publish({"type": "stage", "stage": "generating", "status": "complete"})
        with store._lock:
            active(True)
            with store._db(lease) as db:
                verify_inputs(db)
                if cancel.is_set():
                    raise SessionError("generation_interrupted")
                db.execute("INSERT OR REPLACE INTO analysis_results VALUES(?,?,?,?,?)",
                           (kind, state["target"], state["revision"], state["fingerprint"], payload))
                db.execute("UPDATE analysis_requests SET status='complete',payload=? WHERE id=? AND status='running'",
                           (payload, request_id))
                store._capacity(lease)
        publish({"type": "metadata", "completed": True, "analysis_result": result})
        publish({"type": "done"})
