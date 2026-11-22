from app.config.settings import Settings
from app.vectorstore import mongo_client


def test_mongo_client_uses_bounded_timeouts_and_closes_on_shutdown(monkeypatch):
    mongo_client.close_mongo_clients()
    captured = {}

    class FakeMongoClient:
        def __init__(self, uri, **options):
            captured["uri"] = uri
            captured["options"] = options
            self.closed = False

        def __getitem__(self, name):
            captured["database"] = name
            return object()

        def close(self):
            self.closed = True
            captured["closed"] = True

    monkeypatch.setattr(mongo_client, "MongoClient", FakeMongoClient)
    settings = Settings(
        _env_file=None,
        mongo_uri="mongodb://example.invalid",
        mongo_db_name="test-db",
    )

    mongo_client.get_db(settings)

    options = captured["options"]
    assert options["serverSelectionTimeoutMS"] == 8000
    assert options["connectTimeoutMS"] == 8000
    assert options["socketTimeoutMS"] == 30000
    assert captured["database"] == "test-db"

    mongo_client.close_mongo_clients()

    assert captured["closed"] is True
