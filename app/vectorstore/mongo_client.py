"""MongoDB connection + collection accessors + standard indexes."""
import logging
import threading
from functools import lru_cache

from pymongo import ASCENDING, TEXT, MongoClient
from pymongo.database import Database

from app.config.settings import Settings, get_settings

log = logging.getLogger(__name__)
DOCUMENTS, CHUNKS, SYNC_LOGS = "rag_documents", "rag_chunks", "rag_sync_logs"
INACCESSIBLE_ITEMS = "rag_inaccessible_items"
SYNC_LOCKS = "rag_sync_locks"
_CLIENTS: dict[str, MongoClient] = {}
_CLIENTS_LOCK = threading.Lock()


class MongoConfigError(RuntimeError):
    pass


@lru_cache
def _client(uri: str) -> MongoClient:
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
        socketTimeoutMS=30000,
        appname="sahyog-college-rag",
    )
    with _CLIENTS_LOCK:
        _CLIENTS[uri] = client
    return client


def close_mongo_clients() -> None:
    with _CLIENTS_LOCK:
        clients = tuple(_CLIENTS.values())
        _CLIENTS.clear()
        _client.cache_clear()
    for client in clients:
        client.close()


def get_db(settings: Settings | None = None) -> Database:
    s = settings or get_settings()
    if not s.mongo_uri:
        raise MongoConfigError("MONGO_URI is not set")
    return _client(s.mongo_uri)[s.mongo_db_name]


def init_collections(db: Database) -> None:
    """Idempotent. Creates collections and ordinary (non-Atlas-Search) indexes."""
    existing = set(db.list_collection_names())
    for name in (DOCUMENTS, CHUNKS, SYNC_LOGS, INACCESSIBLE_ITEMS, SYNC_LOCKS):
        if name not in existing:
            db.create_collection(name)
    db[DOCUMENTS].create_index([("file_id", ASCENDING)], unique=True, name="uniq_file_id")
    db[DOCUMENTS].create_index([("status", ASCENDING), ("approved", ASCENDING)], name="status_approved")
    db[DOCUMENTS].create_index([("doc_hash", ASCENDING)], name="doc_hash")
    db[CHUNKS].create_index([("document_id", ASCENDING), ("version", ASCENDING)], name="doc_version")
    db[CHUNKS].create_index([("content_hash", ASCENDING)], name="content_hash")
    # Fallback keyword index that works on every tier (used if Atlas Search index is absent).
    db[CHUNKS].create_index([("text", TEXT), ("document_name", TEXT)], name="text_fallback",
                            default_language="none")
    db[SYNC_LOGS].create_index([("started_at", ASCENDING)], name="started_at")
    db[INACCESSIBLE_ITEMS].create_index([("id", ASCENDING)], name="inaccessible_id")
