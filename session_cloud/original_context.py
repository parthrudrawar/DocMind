"""Original context projection/expansion; only SQL row acquisition is injected.

Rows must already belong to the authorized workspace and selected documents.
No deployment settings, global database session or caller-supplied path is used.
"""
from dataclasses import dataclass


@dataclass
class RetrievedChunk:
    """One chunk with all metadata needed to build LLM context + citations."""
    chunk_id:    str
    doc_id:      str
    filename:    str
    title:       str
    page_number: int
    page_end:    int
    section:     str
    text:        str
    score:       float
    token_count: int = 0
    chunk_index: int | None = None
    parent_chunk_id: str | None = None
    chunk_type: str = "text"
    is_table: bool = False
    is_ocr: bool = False


def assemble_context(
    ranked_chunks: list[tuple[str, float]],   # [(chunk_id, score), ...]
    rows: list,
) -> list[RetrievedChunk]:
    """
    FAISS and BM25 only return chunk_ids + scores — no text.
    This function fetches the actual text and metadata from SQLite
    so the LLM has something to read and we can generate citations.

    Returns chunks in rank order (highest score first).
    """
    if not ranked_chunks:
        return []

    chunk_ids = [cid for cid, _ in ranked_chunks]
    score_map = {cid: score for cid, score in ranked_chunks}

    rows = [(chunk, doc) for chunk, doc in rows if chunk.chunk_id in chunk_ids]

    results = [
        RetrievedChunk(
            chunk_id    = chunk.chunk_id,
            doc_id      = chunk.doc_id,
            filename    = doc.filename,
            title       = doc.title or doc.filename,
            page_number = chunk.page_number,
            page_end    = chunk.page_end or chunk.page_number,
            section     = chunk.section,
            text        = chunk.text,
            score       = score_map.get(chunk.chunk_id, 0.0),
            token_count = chunk.token_count or len(chunk.text.split()),
            chunk_index = chunk.chunk_index,
            parent_chunk_id = chunk.parent_chunk_id,
            chunk_type  = chunk.chunk_type or "text",
            is_table    = bool(chunk.is_table),
            is_ocr      = bool(chunk.is_ocr),
        )
        for chunk, doc in rows
    ]

    # Restore rank order (DB query doesn't preserve it)
    results.sort(key=lambda x: x.score, reverse=True)
    return results


def expand_context(
    selected: list[RetrievedChunk],
    rows: list,
    neighbor_window: int = 1,
    include_parents: bool = True,
    token_budget: int = 4500,
) -> list[RetrievedChunk]:
    """Add nearby children and bounded parents after reranking."""
    if not selected:
        return []

    parent_ids = {chunk.parent_chunk_id for chunk in selected if chunk.parent_chunk_id}
    if not parent_ids:
        return selected

    rows = [(chunk, doc) for chunk, doc in rows
            if chunk.parent_chunk_id in parent_ids or chunk.chunk_id in parent_ids]

    by_id: dict[str, RetrievedChunk] = {}
    children_by_parent: dict[str, list[RetrievedChunk]] = {}
    for chunk, doc in rows:
        item = RetrievedChunk(
            chunk_id=chunk.chunk_id,
            doc_id=chunk.doc_id,
            filename=doc.filename,
            title=doc.title or doc.filename,
            page_number=chunk.page_number,
            page_end=chunk.page_end or chunk.page_number,
            section=chunk.section,
            text=chunk.text,
            score=0.0,
            token_count=chunk.token_count or len(chunk.text.split()),
            chunk_index=chunk.chunk_index,
            parent_chunk_id=chunk.parent_chunk_id,
            chunk_type=chunk.chunk_type or "text",
            is_table=bool(chunk.is_table),
            is_ocr=bool(chunk.is_ocr),
        )
        by_id[item.chunk_id] = item
        if item.parent_chunk_id:
            children_by_parent.setdefault(item.parent_chunk_id, []).append(item)

    for children in children_by_parent.values():
        children.sort(key=lambda item: item.chunk_index if item.chunk_index is not None else -1)

    expanded: list[RetrievedChunk] = list(selected)
    selected_ids = {chunk.chunk_id for chunk in selected}

    # Neighbors are added before parents because they preserve precise pages
    # and usually add less duplicated text.
    for source in selected:
        siblings = children_by_parent.get(source.parent_chunk_id or "", [])
        source_position = next(
            (i for i, sibling in enumerate(siblings) if sibling.chunk_id == source.chunk_id),
            None,
        )
        if source_position is None:
            continue
        start = max(0, source_position - neighbor_window)
        end = min(len(siblings), source_position + neighbor_window + 1)
        for neighbor in siblings[start:end]:
            if neighbor.chunk_id in selected_ids:
                continue
            neighbor.score = source.score
            expanded.append(neighbor)
            selected_ids.add(neighbor.chunk_id)

    if include_parents:
        for source in selected:
            parent_id = source.parent_chunk_id
            parent = by_id.get(parent_id or "")
            if parent and parent.chunk_id not in selected_ids:
                parent.score = source.score
                expanded.append(parent)
                selected_ids.add(parent.chunk_id)

    bounded: list[RetrievedChunk] = []
    used_tokens = 0
    for chunk in expanded:
        cost = max(chunk.token_count, 1)
        if bounded and used_tokens + cost > token_budget:
            continue
        bounded.append(chunk)
        used_tokens += cost
    return bounded
