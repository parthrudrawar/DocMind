"""Storage-independent reciprocal rank fusion, shared by both deployments."""


def reciprocal_rank_fusion(list_a, list_b, k: int = 60, top_k: int = 100):
    scores: dict[str, float] = {}
    for hits in (list_a, list_b):
        for rank, (chunk_id, _) in enumerate(hits):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
