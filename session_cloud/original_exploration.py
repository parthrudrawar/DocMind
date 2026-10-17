"""Original questions/insights/timeline prompts and bounded snapshot projection."""
import re

from app.reasoning.prompt_safety import UNTRUSTED_DOCUMENT_POLICY, wrap_untrusted_document


def exploration_inputs(chunks, documents, kind):
    result = []
    for doc in documents:
        owned = [c for c in chunks if c["doc_id"] == doc["id"]]
        if kind in {"questions", "insights"}:
            section_count, fallback_count, size = (4, 2, 300) if kind == "questions" else (5, 3, 400)
            selected = [c for c in owned if c["level"] == 2][:section_count] or [c for c in owned if c["level"] == 3][:fallback_count]
            excerpts = [c["text"][:size] for c in selected]
            separator = " | " if kind == "questions" else " "
        else:
            selected = [c for c in owned if c["level"] == 1][:1] + [c for c in owned if c["level"] == 3][:2]
            excerpts = [c["text"][:600 if c["level"] == 1 else 400] for c in selected]
            separator = "\n"
        if not selected or not any(excerpts):
            continue
        sources = [{"doc_id": doc["id"], "chunk_id": c["chunk_id"], "filename": doc["filename"],
                    "title": doc["filename"], "page_number": c["page_number"], "page_end": c["page_end"],
                    "section": c["section"], "chunk_type": c["chunk_type"], "exact_quote": excerpt,
                    "text_snippet": excerpt[:250], "provenance_only": True} for c, excerpt in zip(selected, excerpts)]
        result.append({**doc, "text": separator.join(excerpts), "sources": sources})
    return result


def question_messages(documents, topic):
    context = "\n\n".join(f"[{d['filename']}] {d['text']}" for d in documents[:10])
    topic = topic.strip()
    topic_line = (f"The user wants questions specifically about: **{topic}**" if topic else
                  "Cover the most important and interesting topics from the documents.")
    prompt = f"""You are a research advisor helping a user explore their documents.

{UNTRUSTED_DOCUMENT_POLICY}

Document collection snippets:
{wrap_untrusted_document(context, 20_000)}

{topic_line}

Generate exactly 5 smart, specific, insightful questions a student or researcher should ask.
- Questions must be directly answerable from these documents
- Make them specific and thought-provoking, not generic
- Do NOT add category labels, prefixes, or numbering

Return ONLY a JSON array of 5 plain question strings. Example format:
["Question 1?", "Question 2?", "Question 3?", "Question 4?", "Question 5?"]

Respond with only the JSON array, no markdown, no explanation."""
    return [{"role": "system", "content": "You are a research advisor. Return only a valid JSON array of strings. " + UNTRUSTED_DOCUMENT_POLICY},
            {"role": "user", "content": prompt}]


def insight_messages(documents, topic):
    filenames = ", ".join(d["filename"] for d in documents)
    evidence = "\n\n---\n\n".join(f"[{d['filename']}]\n{d['text']}" for d in documents[:12])
    topic = topic.strip()
    topic_line = (f"\nFocus specifically on the topic: **{topic}**\n" if topic else
                  "\nSurface insights across all themes in the documents.\n")
    prompt = f"""You are an expert analyst. You have access to excerpts from these documents:
{filenames}

{UNTRUSTED_DOCUMENT_POLICY}

Excerpts:
{wrap_untrusted_document(evidence, 30_000)}
{topic_line}
Proactively surface 4-7 non-obvious insights the researcher may not think to ask about.
Each insight should be one of:
- "pattern"  — a recurring theme or consistent finding across documents
- "trend"    — a change or progression over time or across studies
- "anomaly"  — something surprising, inconsistent, or unexpected

Return a JSON array. Each item:
{{
  "insight_type": "pattern|trend|anomaly",
  "title": "<insight title in 8 words or less>",
  "description": "<2-3 sentences. Be specific — name documents, figures, or concepts.>",
  "supporting_docs": ["<filename1>", "<filename2>"]
}}

Respond with only the JSON array, no markdown."""
    return [{"role": "system", "content": "You are an expert analyst. Return only valid JSON. " + UNTRUSTED_DOCUMENT_POLICY},
            {"role": "user", "content": prompt}]


def timeline_messages(doc):
    prompt = f"""Extract timeline information from this document.

Filename: {doc['filename']}
Upload date: {doc['upload_date']}

{UNTRUSTED_DOCUMENT_POLICY}

Content:
{wrap_untrusted_document(doc['text'], 5000)}

Extract:
1. The publication year or date range mentioned in the document (e.g. "2021", "2019-2022").
   If not found, use the upload date year.
2. 2-3 key findings, claims, or methodological advances from this document.

Return a JSON object:
{{
  "year": "<4-digit year or year range like 2019-2021>",
  "filename": "{doc['filename']}",
  "events": [
    {{"claim": "<one concise key finding or claim>", "importance": "high|medium|low"}}
  ]
}}

Respond with only the JSON object, no markdown."""
    return [{"role": "system", "content": "You are a timeline extractor. Return only valid JSON. " + UNTRUSTED_DOCUMENT_POLICY},
            {"role": "user", "content": prompt}]


def format_timeline(state):
    """Original chronological sort and lowercased first-80-character dedup."""
    def _sort_key(e):
        year_str = str(e.get("year", "9999"))
        m = re.match(r"(\d{4})", year_str)
        return int(m.group(1)) if m else 9999

    sorted_events = sorted(state["raw_events"], key=_sort_key)
    timeline, seen_claims = [], set()
    for doc_event in sorted_events:
        year = doc_event.get("year", "Unknown")
        filename = doc_event.get("filename", "")
        for ev in doc_event.get("events", []):
            claim = ev.get("claim", "").strip()
            if not claim:
                continue
            claim_key = claim.lower()[:80]
            if claim_key in seen_claims:
                continue
            seen_claims.add(claim_key)
            timeline.append({"year": year, "filename": filename, "claim": claim,
                             "importance": ev.get("importance", "medium"),
                             **({"doc_id": doc_event["doc_id"]} if "doc_id" in doc_event else {})})
    return {**state, "timeline": timeline}
