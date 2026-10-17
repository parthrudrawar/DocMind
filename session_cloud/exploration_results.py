"""Validate original analysis fields without inventing source attribution."""
import json
import re

from app.reasoning.citations import citation_numbers
from session_cloud.original_exploration import question_messages, insight_messages, timeline_messages, format_timeline
from session_cloud.runtime import SessionError


def jobs_for(kind, docs, topic):
    if kind == "questions":
        return [(question_messages(docs, topic), docs, .5, 1024)]
    if kind == "insights":
        return [(insight_messages(docs, topic), docs, .4, 1200)]
    return [(timeline_messages(d), d, .2, 1500) for d in docs]


def decode(text):
    raw = text.strip()
    if raw.startswith("```"):
        if not raw.endswith("```") or raw.count("```") != 2:
            raise SessionError("analysis_invalid")
        raw = raw[3:-3]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        value = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        raise SessionError("analysis_invalid") from None

    def inspect(item, depth=0):
        if depth > 8:
            raise SessionError("analysis_invalid")
        if isinstance(item, str) and (citation_numbers(item) or re.search(r"[\x00-\x08\x0b-\x1f]", item)):
            raise SessionError("analysis_invalid")
        for child in item.values() if isinstance(item, dict) else item if isinstance(item, list) else []:
            inspect(child, depth + 1)
    inspect(value)
    return value


def string(item, key, maximum):
    value = item.get(key)
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise SessionError("analysis_invalid")
    return value


def parse_exploration(text, kind, inputs):
    value = decode(text)
    if kind == "timeline":
        if (not isinstance(value, dict) or value.get("filename") != inputs["filename"]
                or not isinstance(value.get("year"), str)
                or not re.fullmatch(r"\d{4}(?:-\d{4})?|Unknown", value["year"])
                or not isinstance(value.get("events"), list) or len(value["events"]) > 10):
            raise SessionError("analysis_invalid")
        events = []
        for item in value["events"]:
            if not isinstance(item, dict) or item.get("importance", "medium") not in {"high", "medium", "low"}:
                raise SessionError("analysis_invalid")
            events.append({"claim": string(item, "claim", 1000), "importance": item.get("importance", "medium")})
        return [{"year": value["year"], "filename": inputs["filename"], "doc_id": inputs["id"], "events": events}]
    if not isinstance(value, list) or len(value) > 20:
        raise SessionError("analysis_invalid")
    result = []
    if kind == "questions":
        for item in value[:5]:  # Original accepts strings or legacy question objects.
            item = {"question": item} if isinstance(item, str) else item
            if not isinstance(item, dict):
                raise SessionError("analysis_invalid")
            result.append({"question": string(item, "question", 500), "category": "smart"})
    else:
        names = {d["filename"]: d["id"] for d in inputs}  # Original reverse map (last matching filename).
        for item in value:
            if (not isinstance(item, dict) or item.get("insight_type") not in {"pattern", "trend", "anomaly"}
                    or not isinstance(item.get("supporting_docs"), list) or len(item["supporting_docs"]) > 12
                    or any(not isinstance(n, str) or n not in names for n in item["supporting_docs"])):
                raise SessionError("analysis_invalid")
            result.append({"insight_type": item["insight_type"], "title": string(item, "title", 200),
                "description": string(item, "description", 2400), "supporting_docs": item["supporting_docs"],
                "supporting_document_ids": [names[n] for n in item["supporting_docs"]]})
        # Original API orders by insight_type, then newest creation; one atomic
        # run uses one timestamp, retaining model order for ties.
        result.sort(key=lambda i: i["insight_type"])
    return result


def finish_exploration(items, kind):
    return format_timeline({"raw_events": items})["timeline"] if kind == "timeline" else items
