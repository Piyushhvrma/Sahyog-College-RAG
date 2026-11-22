from app.rag.query_rewriter import rewrite_query
from app.retrieval.hybrid_search import apply_boosts, dedupe, query_hints, to_source
from app.vectorstore.vector_search import vector_search


def test_rewrite_expands_shorthand():
    q = rewrite_query("OS pyq sem 5")
    assert "previous year question paper" in q and "semester" in q


def test_rewrite_uses_history_for_followups():
    q = rewrite_query("what about sem 6?", ["operating systems PYQ for CSE"])
    assert "operating systems" in q


def test_rewrite_ignores_history_for_standalone():
    q = rewrite_query("What is the minimum attendance required for end semester exams at NIT Raipur", ["old"])
    assert not q.startswith("old")


def test_hints():
    h = query_hints("CSE semester 5 PYQ 2023-24")
    assert h == {"branch": "CSE", "semester": 5, "academic_year": "2023-24", "doc_type": "pyq"}


def test_boost_reorders():
    res = [{"_id": 1, "_rrf": 0.0300, "semester": 3}, {"_id": 2, "_rrf": 0.0290, "semester": 5}]
    out = apply_boosts(res, {"semester": 5})
    assert out[0]["_id"] == 2


def test_dedupe_hash_and_per_doc_cap():
    rows = [{"_id": i, "document_id": "d1", "content_hash": f"h{i}"} for i in range(5)]
    rows.append({"_id": 9, "document_id": "d2", "content_hash": "h0"})  # dup hash of first
    out = dedupe(rows, max_per_doc=3)
    assert [r["_id"] for r in out] == [0, 1, 2]


def test_private_link_hidden_unless_enabled():
    r = {"_id": "c", "text": "t", "document_id": "d", "file_name": "f.pdf", "page_number": 2,
         "source_url": "https://drive.google.com/x", "_rrf": 0.1}
    assert to_source(r, False)["source_url"] is None
    assert to_source(r, True)["source_url"] == "https://drive.google.com/x"
    assert "embedding" not in to_source(r, True)


def test_vector_search_cannot_override_approved_only_filter():
    class Collection:
        pipeline = None

        def aggregate(self, pipeline):
            self.pipeline = pipeline
            return []

    class Database:
        def __init__(self):
            self.collection = Collection()

        def __getitem__(self, _name):
            return self.collection

    db = Database()
    vector_search(
        db,
        [0.0] * 768,
        filters={"approved": False, "branch": "CSE"},
    )

    assert db.collection.pipeline[0]["$vectorSearch"]["filter"] == {
        "approved": True,
        "branch": "CSE",
    }
