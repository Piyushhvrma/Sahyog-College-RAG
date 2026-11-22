"""Create collections + normal indexes, then try to create the Atlas Search indexes and print their JSON.
Usage: python -m scripts.setup_mongo"""
import json

from app.vectorstore.index_manager import (TEXT_INDEX_NAME, VECTOR_INDEX_NAME, create_search_indexes,
                                           list_search_indexes, text_index_definition,
                                           vector_index_definition)
from app.vectorstore.mongo_client import get_db, init_collections
from scripts._common import bootstrap


def main() -> int:
    s = bootstrap("mongo")
    db = get_db(s)
    db.command("ping")
    print("[OK] Connected to MongoDB")
    init_collections(db)
    print("[OK] Collections and standard indexes ready")
    for name, status in create_search_indexes(db, s.embedding_dimensions).items():
        print(f"  {name}: {status}")
    print("\nCurrent Atlas search indexes:")
    for name, idx in list_search_indexes(db).items():
        print(f"  {name}: status={idx.get('status')} queryable={idx.get('queryable')}")
    print(f"\nIf creation failed, paste these in Atlas UI (collection rag_chunks):\n"
          f"--- Vector Search index '{VECTOR_INDEX_NAME}' ---\n"
          f"{json.dumps(vector_index_definition(s.embedding_dimensions), indent=2)}\n"
          f"--- Atlas Search index '{TEXT_INDEX_NAME}' ---\n{json.dumps(text_index_definition(), indent=2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
