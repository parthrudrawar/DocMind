"""Original Source Guide and Collection Brief prompts and input construction."""
import json

from session_cloud.runtime import SessionError


def source_guide_prompt(l1_text):
    prompt = (
        f"Read this document excerpt and return a JSON object with exactly two keys:\n"
        f"- \"summary\": a 2-3 sentence summary of what this document covers\n"
        f"- \"key_topics\": an array of 3-5 key topics or concepts discussed\n\n"
        f"Excerpt:\n{l1_text[:800]}\n\n"
        f"Return ONLY valid JSON, no markdown, no explanation."
    )
    return prompt


def parse_source_guide(raw):
    # Original fenced-JSON handling. Extra validation rejects malformed model
    # output rather than publishing an empty or incorrectly typed guide.
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw.removeprefix("json")
    try:
        data = json.loads(raw)
        if (not isinstance(data, dict) or set(data) != {"summary", "key_topics"}
                or not isinstance(data["summary"], str) or not 1 <= len(data["summary"].strip()) <= 2500
                or not isinstance(data["key_topics"], list) or not 3 <= len(data["key_topics"]) <= 5
                or any(not isinstance(t, str) or not 1 <= len(t.strip()) <= 120 for t in data["key_topics"])):
            raise ValueError
        return data
    except (TypeError, ValueError):
        raise SessionError("analysis_invalid") from None


def collection_parts(docs, guides):
    """Only SQL guide acquisition is replaced by an authorized result mapping."""
    parts = []
    for doc in docs:
        guide = guides.get(doc.doc_id)
        if guide and guide.summary:
            topics = json.loads(guide.key_topics or "[]")
            topic_str = ", ".join(topics) if topics else "general topics"
            parts.append(f"- {doc.filename}: {guide.summary} (Topics: {topic_str})")
        else:
            parts.append(f"- {doc.filename}: (no guide available)")
    return parts


def collection_brief_prompt(parts):
    summaries = "\n".join(parts)
    prompt = (
        f"Based on these document summaries from a research collection, "
        f"write a 3-sentence briefing that describes:\n"
        f"1. What this collection is about overall\n"
        f"2. The key themes or findings\n"
        f"3. What questions this collection can help answer\n\n"
        f"Documents:\n{summaries}\n\n"
        f"Return ONLY the 3-sentence briefing, no labels or headings."
    )
    return prompt
