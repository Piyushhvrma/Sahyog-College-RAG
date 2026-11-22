import pytest
from app.retrieval.rrf import rrf_fuse


def d(i, **kw):
    return {"_id": i, **kw}


def test_doc_in_both_lists_wins():
    fused = rrf_fuse([[d("a"), d("b"), d("c")], [d("c"), d("d"), d("a")]])
    assert fused[0]["_id"] in {"a", "c"}
    assert {x["_id"] for x in fused} == {"a", "b", "c", "d"}


def test_score_formula():
    fused = rrf_fuse([[d("a")], [d("a")]], k=60)
    assert fused[0]["_rrf"] == pytest.approx(2 / 61)


def test_weights():
    fused = rrf_fuse([[d("a")], [d("b")]], k=60, weights=[1.0, 3.0])
    assert fused[0]["_id"] == "b"


def test_duplicate_within_list_counted_once():
    fused = rrf_fuse([[d("a"), d("a")]], k=60)
    assert fused[0]["_rrf"] == pytest.approx(1 / 61)


def test_empty_and_one_empty():
    assert rrf_fuse([[], []]) == []
    assert [x["_id"] for x in rrf_fuse([[d("a")], []])] == ["a"]


def test_merges_engine_scores():
    fused = rrf_fuse([[d("a", _vscore=0.9)], [d("a", _kscore=4.2)]])
    assert fused[0]["_vscore"] == 0.9 and fused[0]["_kscore"] == 4.2


def test_bad_args():
    with pytest.raises(ValueError):
        rrf_fuse([[d("a")]], k=0)
    with pytest.raises(ValueError):
        rrf_fuse([[d("a")]], weights=[1, 2])
