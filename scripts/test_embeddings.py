"""Verify Gemini key/model/dimensions. Prints dimension, never the vector.
  python -m scripts.test_embeddings"""
from app.embeddings.gemini_embedder import EmbeddingError, GeminiEmbedder
from scripts._common import bootstrap


def main() -> int:
    s = bootstrap("gemini")
    emb = GeminiEmbedder(s)
    text = "What are the attendance requirements at NIT Raipur?"
    print(f"Input text : {text}")
    print(f"Model      : {emb.model}")
    print(f"Expected   : {emb.dimensions} dimensions")
    try:
        qv = emb.embed_query(text)
        dv = emb.embed_documents(["Students must maintain at least 75% attendance."], ["Attendance Rules"])[0]
        sim = sum(a * b for a, b in zip(qv, dv))
        print(f"Query vec  : {len(qv)} dims | Document vec: {len(dv)} dims")
        print(f"Cosine sim (query vs attendance doc): {sim:.4f}")
        print("Result     : SUCCESS")
        return 0
    except (EmbeddingError, ValueError) as exc:
        print(f"Result     : FAILURE — {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
