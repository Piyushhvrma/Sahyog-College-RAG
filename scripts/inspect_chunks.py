"""Look at what is actually stored for a document.
  python -m scripts.inspect_chunks --name "Operating"      # fuzzy file-name match
  python -m scripts.inspect_chunks --file-id <id> --full"""
import argparse
import re

from app.vectorstore.mongo_client import CHUNKS, DOCUMENTS, get_db
from scripts._common import bootstrap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name")
    ap.add_argument("--file-id")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--max", type=int, default=10)
    a = ap.parse_args()
    s = bootstrap("mongo")
    db = get_db(s)
    q = {"file_id": a.file_id} if a.file_id else {"file_name": {"$regex": re.escape(a.name or ""), "$options": "i"}}
    docs = list(db[DOCUMENTS].find(q).limit(5))
    if not docs:
        print("No matching documents.")
        return 1
    for d in docs:
        print(f"\n## {d['file_name']}  status={d.get('status')} approved={d.get('approved')} "
              f"chunks={d.get('chunk_count')} model={d.get('embedding_model')}/{d.get('embedding_dimensions')}")
        print(f"   path={d.get('folder_path')!r} branch={d.get('branch')} sem={d.get('semester')} "
              f"subject={d.get('subject')} year={d.get('academic_year')} type={d.get('doc_type')}")
        if d.get("error"):
            print(f"   ERROR: {d['error']}")
        for c in db[CHUNKS].find({"document_id": d["file_id"]}, {"embedding": 0}).sort("chunk_index", 1).limit(a.max):
            body = c["text"] if a.full else c["text"][:200].replace("\n", " ") + "…"
            print(f"   [{c['chunk_index']}] page={c.get('page_number')} heading={c.get('heading')!r}\n       {body}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
