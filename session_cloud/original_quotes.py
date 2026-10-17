"""Original sentence selection; provider fallback is explicitly injected per request."""
import logging
import re
from app.reasoning.citations import first_citation_position

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the","a","an","is","are","was","were","of","in","to","and","or","it",
    "this","that","with","for","on","at","by","from","be","has","have","had",
    "not","but","as","its","also","which","their","they","we","he","she","i",
    "do","did","so","if","about","can","more","than","into","will","been",
}


def _extract_answer_context(answer: str, citation_number: int) -> str:
    """Extract the 60-word window around the [n] citation marker in the answer."""
    pos = first_citation_position(answer, citation_number)
    if pos is None:
        return answer[:300]

    words = answer.split()
    char_count = 0
    marker_word_idx = 0
    for idx, w in enumerate(words):
        char_count += len(w) + 1
        if char_count >= pos:
            marker_word_idx = idx
            break

    start = max(0, marker_word_idx - 30)
    end   = min(len(words), marker_word_idx + 30)
    return " ".join(words[start:end])


def _score_sentence(sentence: str, context: str) -> int:
    """Count non-stopword token overlaps between sentence and answer context."""
    sent_tokens    = {w.lower().strip(".,;:!?()[]\"'") for w in sentence.split()} - _STOPWORDS
    context_tokens = {w.lower().strip(".,;:!?()[]\"'") for w in context.split()}  - _STOPWORDS
    return len(sent_tokens & context_tokens)


def _extract_exact_quote(chunk_text: str, answer_context: str, fallback) -> str:
    """
    Find the 1-3 sentences in chunk_text that best support the answer_context.

    Pass 1: keyword overlap scoring (instant, no LLM)
    Pass 2: LLM fallback if Pass 1 yields < 12 words
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", chunk_text) if s.strip()]
    if not sentences:
        return ""

    scored = [(s, _score_sentence(s, answer_context)) for s in sentences]
    scored.sort(key=lambda x: x[1], reverse=True)

    best_sent = scored[0][0]
    best_idx  = sentences.index(best_sent)

    result_sentences = [best_sent]
    if scored[0][1] > 0:
        if best_idx + 1 < len(sentences) and _score_sentence(sentences[best_idx + 1], answer_context) > 0:
            result_sentences.append(sentences[best_idx + 1])
        elif best_idx - 1 >= 0 and _score_sentence(sentences[best_idx - 1], answer_context) > 0:
            result_sentences = [sentences[best_idx - 1]] + result_sentences

    exact_quote = " ".join(result_sentences)

    words = exact_quote.split()
    if len(words) > 80:
        exact_quote = " ".join(words[:80]) + "…"

    if len(exact_quote.split()) < 12:
        logger.info(f"[exact_quote] Pass 1 too short ({len(exact_quote.split())} words), using LLM fallback")
        exact_quote = fallback(chunk_text, answer_context)
    else:
        logger.info(f"[exact_quote] Pass 1 succeeded ({len(exact_quote.split())} words)")

    return exact_quote
