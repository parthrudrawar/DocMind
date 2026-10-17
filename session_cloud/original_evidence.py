import math
from typing import Any


def _field(item: Any, name: str, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def assess_evidence_strength(chunks: list, citations: list[dict]) -> dict:
    """Describe retrieval support without presenting it as model probability."""
    if not chunks:
        return {
            "level": "none",
            "label": "No supporting evidence",
            "cited_sources": 0,
            "positive_cited_sources": 0,
            "explanation": "No document passages were retrieved for this answer.",
        }

    cited_numbers = {
        citation.get("number")
        for citation in citations
        if isinstance(citation, dict) and isinstance(citation.get("number"), int)
    }
    cited_chunks = []
    seen_chunk_ids = set()
    for number in sorted(cited_numbers):
        if number < 1 or number > len(chunks):
            continue
        chunk = chunks[number - 1]
        chunk_id = _field(chunk, "chunk_id", number)
        if chunk_id in seen_chunk_ids:
            continue
        cited_chunks.append(chunk)
        seen_chunk_ids.add(chunk_id)

    if not cited_chunks:
        return {
            "level": "limited",
            "label": "Retrieved evidence is uncited",
            "cited_sources": 0,
            "positive_cited_sources": 0,
            "explanation": "Passages were retrieved, but the answer contains no valid source references.",
        }

    scores = []
    for chunk in cited_chunks:
        try:
            score = float(_field(chunk, "score"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(score):
            scores.append(score)

    positive_count = sum(score > 0 for score in scores)
    common = {
        "cited_sources": len(cited_chunks),
        "positive_cited_sources": positive_count,
        "score_type": "raw_cross_encoder_logit",
        "raw_score_min": min(scores) if scores else None,
        "raw_score_max": max(scores) if scores else None,
        "explanation": (
            "Based on cited retrieval passages and raw cross-encoder relevance "
            "signals; this is not a probability or a measure of model certainty."
        ),
    }
    if not scores:
        return {
            "level": "unrated",
            "label": "Cited evidence (unrated)",
            **common,
            "explanation": (
                "The answer cites retrieved passages, but no finite retrieval score "
                "was available; this does not measure model certainty."
            ),
        }
    if positive_count >= 2:
        return {"level": "strong", "label": "Multiple cited matches", **common}
    if positive_count == 1:
        return {"level": "moderate", "label": "One cited match", **common}
    return {"level": "weak", "label": "Weak cited matches", **common}
