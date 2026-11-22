"""Run sample college questions through hybrid retrieval.
  python -m scripts.test_retrieval
  python -m scripts.test_retrieval --query "OS PYQ semester 5" --branch CSE"""
import argparse

from app.embeddings.gemini_embedder import GeminiEmbedder
from app.retrieval.hybrid_search import hybrid_search
from app.vectorstore.mongo_client import get_db
from app.vectorstore.vector_search import build_filter
from scripts._common import bootstrap

SAMPLE_QUESTIONS = [
    "What is the minimum attendance required to sit in the end semester exam?",
    "Show me previous year questions for Operating Systems in semester 5",
    "What is the placement process and eligibility criteria at NIT Raipur?",
    "What is the timetable for CSE semester 3?",
    "Where can I find notes for Data Structures?",
    "Latest notice about the examination schedule",
    "What is the grading system and how is CGPA calculated?",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query")
    ap.add_argument("--branch")
    ap.add_argument("--semester", type=int)
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()
    s = bootstrap("mongo", "gemini")
    db, emb = get_db(s), GeminiEmbedder(s)
    flt = build_filter(branch=args.branch, semester=args.semester)
    for q in ([args.query] if args.query else SAMPLE_QUESTIONS):
        out = hybrid_search(db, emb, q, top_k=args.top_k, filters=flt, settings=s)
        print(f"\n=== {q}\n    rewritten: {out['rewritten_query']}\n    {out['retrieval']}")
        if not out["results"]:
            print("    (no results — is anything indexed/approved yet? are Atlas indexes READY?)")
        for r in out["results"]:
            pg = f" p.{r['page_number']}" if r["page_number"] else ""
            print(f"  • [{r['score']:.4f}] {r['document_name']}{pg}")
            print(f"      {r['text'][:140].replace(chr(10), ' ')}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
