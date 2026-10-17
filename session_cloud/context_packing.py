"""Complete-passage packing; never cut source text or reuse old source numbers."""

from session_cloud.index_contract import IndexFailure
from session_cloud.original_prompts import aggregator_node


def pack_passages(
    primary, expanded, *, count_tokens, byte_limit=32_000, token_limit=8192
):
    def state(items):
        return aggregator_node({"retrieved_chunks": items})

    def fits(items):
        text = state(items)["aggregated_evidence"]
        return (
            len(text) <= 50_000
            and len(text.encode("utf-8")) <= byte_limit
            and count_tokens(text) <= token_limit
        )

    if not expanded:
        return [], state([]), {"packed": False, "included": 0, "omitted": 0}
    if fits(expanded):
        return (
            expanded,
            state(expanded),
            {"packed": False, "included": len(expanded), "omitted": 0},
        )
    # Protect the strongest passage from EACH represented document, not just
    # the first document in a comparison. Primary order is cross-encoder order.
    ordered = list({c.chunk_id: c for c in [*primary, *expanded]}.values())
    required, docs = [], set()
    for chunk in primary:
        if chunk.doc_id not in docs:
            required.append(chunk)
            docs.add(chunk.doc_id)
    if not required or not fits(required):
        raise IndexFailure("context_limit")
    selected = list(required)
    ids = {c.chunk_id for c in selected}
    for chunk in ordered:
        if chunk.chunk_id in ids:
            continue
        # Parents often repeat selected children. Keep precise-page evidence
        # before optional parent/neighbor context, all with unchanged text.
        if any(c.doc_id == chunk.doc_id and c.text in chunk.text for c in selected):
            continue
        if fits([*selected, chunk]):
            selected.append(chunk)
            ids.add(chunk.chunk_id)
    return (
        selected,
        state(selected),
        {
            "packed": True,
            "included": len(selected),
            "omitted": len({c.chunk_id for c in ordered}) - len(selected),
        },
    )
