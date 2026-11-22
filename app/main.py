import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import close_embedder, internal, public
from app.config.settings import get_settings
from app.drive.runner import SyncRunner
from app.vectorstore.mongo_client import close_mongo_clients


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            try:
                await asyncio.to_thread(app.state.sync_runner.shutdown)
            finally:
                try:
                    close_embedder()
                finally:
                    await asyncio.to_thread(close_mongo_clients)

    # No CORS middleware on purpose: browsers must never call this service directly.
    app = FastAPI(title="Sahyog College RAG", version="1.0.0",
                  docs_url=None if settings.environment == "production" else "/docs",
                  redoc_url=None, openapi_url=None if settings.environment == "production" else "/openapi.json",
                  lifespan=lifespan)
    app.state.sync_runner = SyncRunner()
    app.include_router(public)
    app.include_router(internal)
    return app


app = create_app()
