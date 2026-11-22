"""Reciprocal Rank Fusion, implemented in Python (no reliance on Atlas $rankFusion)."""
from typing import Any, Sequence


def rrf_fuse(ranked_lists: Sequence[Sequence[dict]], k: int = 60, id_key: str = "_id",
             weights: Sequence[float] | None = None) -> list[dict]:
    """score(d) = Σ weight_i / (k + rank_i(d)), rank starting at 1.
    Returns docs (first-seen copy) sorted by descending fused score, with `_rrf` set."""
    if k <= 0:
        raise ValueError("k must be positive")
    weights = list(weights) if weights is not None else [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights must match number of lists")
    scores: dict[Any, float] = {}
    docs: dict[Any, dict] = {}
    for lst, w in zip(ranked_lists, weights):
        seen_in_list: set[Any] = set()
        for rank, doc in enumerate(lst, start=1):
            key = doc[id_key]
            if key in seen_in_list:   # a list should only count a doc once
                continue
            seen_in_list.add(key)
            scores[key] = scores.get(key, 0.0) + w / (k + rank)
            if key not in docs:
                docs[key] = dict(doc)
            else:
                # merge per-engine scores (vector + keyword) onto one record
                for extra in ("_vscore", "_kscore"):
                    if extra in doc:
                        docs[key][extra] = doc[extra]
    fused = []
    for key, d in docs.items():
        d["_rrf"] = scores[key]
        fused.append(d)
    fused.sort(key=lambda d: d["_rrf"], reverse=True)
    return fused
