"""Hybrid retrieval: embed -> vector search + keyword search -> RRF -> boosts -> dedupe -> top-k."""
import logging
from typing import Any, Optional

from pymongo.database import Database

from app.config.settings import Settings, get_settings
from app.embeddings.gemini_embedder import GeminiEmbedder
from app.ingestion.metadata import (extract_academic_year, extract_branch, extract_doc_type,
                                    extract_semester)
from app.rag.query_rewriter import rewrite_query
from app.retrieval.keyword_search import keyword_search
from app.retrieval.rrf import rrf_fuse
from app.vectorstore.vector_search import build_filter, vector_search

log = logging.getLogger(__name__)
BOOST = 0.15          # relative boost per metadata field matching a hint in the query
MAX_PER_DOC = 3


def query_hints(query: str) -> dict[str, Any]:
    """Soft hints parsed from the question text (used as boosts, not hard filters)."""
    words = [w for w in query.replace("/", " ").split()]
    return {k: v for k, v in {
        "branch": extract_branch(words),
        "semester": extract_semester(query),
        "academic_year": extract_academic_year(query),
        "doc_type": extract_doc_type("", query),
    }.items() if v is not None}


def apply_boosts(results: list[dict], hints: dict[str, Any]) -> list[dict]:
    if not hints:
        return results
    for r in results:
        matches = sum(1 for k, v in hints.items() if r.get(k) == v)
        r["_final"] = r["_rrf"] * (1 + BOOST * matches)
        r["_boosts"] = matches
    results.sort(key=lambda r: r["_final"], reverse=True)
    return results


def dedupe(results: list[dict], max_per_doc: int = MAX_PER_DOC) -> list[dict]:
    seen_hash: set[str] = set()
    per_doc: dict[str, int] = {}
    out = []
    for r in results:
        h = r.get("content_hash")
        if h and h in seen_hash:
            continue
        d = r.get("document_id", "")
        if per_doc.get(d, 0) >= max_per_doc:
            continue
        if h:
            seen_hash.add(h)
        per_doc[d] = per_doc.get(d, 0) + 1
        out.append(r)
    return out


def select_results(results: list[dict], top_k: int, max_per_doc: int = MAX_PER_DOC) -> list[dict]:
    """Diverse-first selection that backfills to top_k.

    The per-document cap keeps one document from crowding out others, but when few documents
    match (e.g. a subject with a single approved notes file) the cap alone discarded relevant
    chunks: 9 keyword hits from DS U4.pdf became 3 results. Remaining ranked, non-duplicate
    chunks now fill the leftover slots."""
    selected = dedupe(results, max_per_doc)[:top_k]
    if len(selected) < top_k:
        chosen = {id(r) for r in selected}
        seen_hash = {r.get("content_hash") for r in selected if r.get("content_hash")}
        for r in results:
            if len(selected) >= top_k:
                break
            h = r.get("content_hash")
            if id(r) in chosen or (h and h in seen_hash):
                continue
            if h:
                seen_hash.add(h)
            selected.append(r)
    return selected


def order_for_reading(results: list[dict]) -> list[dict]:
    """Group the selected chunks by document (best-ranked document first) and keep each
    document's chunks in page/chunk order, so passages read like the original notes."""
    doc_rank: dict[str, int] = {}
    for position, r in enumerate(results):
        doc_rank.setdefault(r.get("document_id", ""), position)

    def key(r: dict) -> tuple:
        page = r.get("page_number")
        index = r.get("chunk_index")
        return (
            doc_rank[r.get("document_id", "")],
            page if isinstance(page, int) else float("inf"),
            index if isinstance(index, int) else float("inf"),
        )

    return sorted(results, key=key)


def to_source(r: dict, link_visible: bool) -> dict:
    meta_keys = ("branch", "semester", "subject", "subject_code", "academic_year", "doc_type",
                 "study_year", "unit", "chapter", "folder_path", "source_path", "source_type",
                 "heading", "chunk_index", "content_hash", "file_id")
    return {
        "chunk_id": str(r["_id"]),
        "text": r["text"],
        "document_id": r.get("document_id", ""),
        "document_name": r.get("file_name") or r.get("document_name", ""),
        "page_number": r.get("page_number"),
        "source_url": r.get("source_url") if link_visible else None,
        "score": round(float(r.get("_final", r.get("_rrf", 0.0))), 6),
        "metadata": {k: r.get(k) for k in meta_keys if r.get(k) is not None},
    }


def hybrid_search(db: Database, embedder: GeminiEmbedder, query: str, top_k: int = 5,
                  filters: dict[str, Any] | None = None, history: list[str] | None = None,
                  settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    rewritten = rewrite_query(query, history)
    flt = dict(filters or {})
    flt["approved"] = True
    candidates = max(top_k * 4, 20)

    kw_results, kw_engine = keyword_search(
        db,
        rewritten,
        limit=candidates,
        filters=flt,
        index_name=settings.rag_text_search_index_name,
    )
    if settings.vector_search_enabled:
        qvec = embedder.embed_query(rewritten)
        vec_results = vector_search(db, qvec, limit=candidates, filters=flt)
        fused = rrf_fuse([vec_results, kw_results], k=60)
        fusion = "rrf(k=60)"
    else:
        vec_results = []
        fused = rrf_fuse([kw_results], k=60)
        fusion = "keyword_only"
    fused = apply_boosts(fused, query_hints(query))
    final = order_for_reading(select_results(fused, top_k))
    return {
        "query": query,
        "rewritten_query": rewritten,
        "results": [to_source(r, settings.link_visible) for r in final],
        "retrieval": {"vector_hits": len(vec_results), "keyword_hits": len(kw_results),
                      "keyword_engine": kw_engine, "fusion": fusion},
    }
