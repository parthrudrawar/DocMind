"""Original citation field/quote contracts plus fail-closed source validation.

This milestone has no provider fallback. A caller must explicitly supply the
original quote-prompt provider for short excerpts; never fabricate a substitute.
"""
import re

from app.reasoning.citations import citation_numbers
from session_cloud.original_evidence import assess_evidence_strength
from session_cloud.original_quotes import _extract_answer_context, _extract_exact_quote


class AttributionError(Exception):
    def __init__(self, code="citation_invalid"):
        self.code = code
        super().__init__(code)


def _unavailable_fallback(passage, claim):
    raise AttributionError("quote_provider_required")


def _normalized(text):
    return " ".join(text.split())


def format_attribution(answer, chunks, *, fallback=_unavailable_fallback):
    """Caller supplies only the verified context used in this very answer."""
    if not isinstance(answer, str) or not 1 <= len(answer) <= 8000:
        raise AttributionError()
    cited = citation_numbers(answer)
    if (cited - set(range(1, len(chunks) + 1))
            or re.search(r"\[0\]|【\s*0(?:†L\d+(?:-L?\d+)?)?\s*】", answer)):
        raise AttributionError()
    citations = []
    for number, chunk in enumerate(chunks, 1):
        if number not in cited:
            continue
        quote = _extract_exact_quote(chunk.text, _extract_answer_context(answer, number), fallback)
        # The original extractor can append an ellipsis after its 80-word cap.
        # Match the non-empty literal content, ignoring only whitespace and that
        # presentation suffix. An LLM cannot invent quotes, pages or filenames.
        literal = quote[:-1] if quote.endswith("…") else quote
        if not literal.strip() or _normalized(literal) not in _normalized(chunk.text):
            raise AttributionError()
        snippet = chunk.text[:250]
        citations.append({"number": number, "doc_id": chunk.doc_id, "title": chunk.title,
            "filename": chunk.filename, "page_number": chunk.page_number, "page_end": chunk.page_end,
            "section": chunk.section, "chunk_type": chunk.chunk_type,
            "text_snippet": snippet + "…" if len(chunk.text) > 250 else chunk.text,
            "exact_quote": quote})
    return {"citations": citations, "evidence_strength": assess_evidence_strength(chunks, citations)}
