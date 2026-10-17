"""Original contradiction/gap inputs and prompts, without global SQL/LLM state.

Sampling is deliberately unchanged: these analyses do NOT run hybrid search.
Chunk order is the original insertion order preserved in the sealed snapshot.
"""
from app.reasoning.prompt_safety import UNTRUSTED_DOCUMENT_POLICY, wrap_untrusted_document


def comparison_inputs(chunks, documents, kind):
    result = []
    for doc in documents:
        owned = [c for c in chunks if c["doc_id"] == doc["id"]]
        if kind == "contradictions":
            selected = [c for c in owned if c["level"] == 3][:6][:5]
            excerpts = [c["text"][:300] for c in selected]
        else:
            summaries = [c for c in owned if c["level"] == 1]
            selected = summaries[:1] or [c for c in owned if c["level"] == 3][:3]
            # Original joins fallback paragraphs BEFORE the 1500-char limit.
            remaining, excerpts = 1500, []
            kept = []
            for chunk in selected:
                if remaining <= 0:
                    break
                excerpt = chunk["text"][:remaining]
                kept.append(chunk)
                excerpts.append(excerpt)
                remaining -= len(excerpt) + 1
            selected = kept
        if not selected or not any(excerpts):
            continue
        sources = []
        for chunk, excerpt in zip(selected, excerpts):
            sources.append({"doc_id": doc["id"], "chunk_id": chunk["chunk_id"],
                "filename": doc["filename"], "title": doc["filename"],
                "page_number": chunk["page_number"], "page_end": chunk["page_end"],
                "section": chunk["section"], "chunk_type": chunk["chunk_type"],
                "text_snippet": excerpt[:250], "exact_quote": excerpt, "provenance_only": True})
        text = ("\n---\n".join(excerpts) if kind == "contradictions" else
                " ".join(c["text"] for c in (summaries[:1] or [c for c in owned if c["level"] == 3][:3]))[:1500])
        result.append({"id": doc["id"], "filename": doc["filename"], "sources": sources, "text": text})
    return result


def contradiction_messages(a, b):
    evidence_a = wrap_untrusted_document(f"Filename: {a['filename']}\n{a['text']}", 5000)
    evidence_b = wrap_untrusted_document(f"Filename: {b['filename']}\n{b['text']}", 5000)
    prompt = f"""You are a scientific fact-checker comparing two documents.

{UNTRUSTED_DOCUMENT_POLICY}

Document A evidence:
{evidence_a}

Document B evidence:
{evidence_b}

Find DIRECT CONTRADICTIONS — claims in Document A that explicitly contradict claims in Document B.
Ignore minor differences in emphasis, scope, or context.

Return a JSON array (may be empty). Each item:
{{
  "topic": "<short topic label>",
  "text_a": "<exact or paraphrased contradicting sentence from A>",
  "text_b": "<exact or paraphrased contradicting sentence from B>",
  "explanation": "<one-sentence explanation of why these contradict>",
  "confidence": <float 0-1>
}}

Respond with only the JSON array, no markdown."""
    return [{"role": "system", "content": "You are an expert fact-checker. Return only valid JSON. "
             + UNTRUSTED_DOCUMENT_POLICY}, {"role": "user", "content": prompt}]


def gap_messages(documents):
    combined = "\n\n".join(f"[{d['filename']}]\n{d['text']}" for d in documents)
    prompt = f"""You are a research librarian reviewing a collection of documents.

{UNTRUSTED_DOCUMENT_POLICY}

Document collection overview:
{wrap_untrusted_document(combined, 20_000)}

Identify 3-6 KNOWLEDGE GAPS — important topics, methods, or evidence that are
ABSENT or insufficiently covered given what these documents address.
Focus on gaps that would be meaningful to a researcher using this collection.

Return a JSON array. Each item:
{{
  "topic": "<gap topic in 5 words or less>",
  "description": "<2-3 sentences explaining what is missing and why it matters>"
}}

Respond with only the JSON array, no markdown."""
    return [{"role": "system", "content": "You are a research librarian. Return only valid JSON. "
             + UNTRUSTED_DOCUMENT_POLICY}, {"role": "user", "content": prompt}]
