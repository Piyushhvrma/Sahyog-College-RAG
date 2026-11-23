"""Central configuration (pydantic-settings v2). All paths resolve relative to the project root."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    rag_service_secret: str = ""

    mongo_uri: str = ""
    mongo_db_name: str = "sahyog_rag"

    gemini_api_key: str = ""
    embedding_model: str = "gemini-embedding-2"
    embedding_dimensions: int = 768

    google_credentials_path: str = "./secrets/credentials.json"
    google_token_path: str = "./secrets/token.json"
    drive_root_folder_id: str = ""
    drive_interactive_auth: bool = True
    max_download_mb: int = 50
    local_knowledge_dir: str = "./knowledge"

    tesseract_cmd: str = ""
    ocr_enabled: bool = True

    chunk_target_tokens: int = Field(default=800, ge=100, le=4000)
    chunk_overlap_tokens: int = Field(default=100, ge=0)

    search_top_k: int = Field(default=5, ge=1, le=10)
    rag_text_search_index_name: str = "rag_text_index"
    vector_search_enabled: bool = True
    link_visible: bool = False
    auto_approve: bool = False
    auto_sync_enabled: bool = False
    sync_interval_minutes: int = Field(default=360, ge=1, le=10080)
    embed_batch_size: int = Field(default=16, ge=1, le=100)
    embed_min_interval_s: float = Field(default=0.7, ge=0)

    @field_validator("embedding_dimensions")
    @classmethod
    def _dims(cls, v: int) -> int:
        if v < 64 or v > 3072:
            raise ValueError("EMBEDDING_DIMENSIONS must be between 64 and 3072")
        return v

    @field_validator("chunk_overlap_tokens")
    @classmethod
    def _overlap(cls, v: int, info) -> int:
        target = info.data.get("chunk_target_tokens", 800)
        if v >= target:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_TARGET_TOKENS")
        return v

    # ---- helpers -------------------------------------------------
    @staticmethod
    def _resolve(p: str) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    @property
    def credentials_file(self) -> Path:
        return self._resolve(self.google_credentials_path)

    @property
    def token_file(self) -> Path:
        return self._resolve(self.google_token_path)

    @property
    def local_knowledge_path(self) -> Path:
        return self._resolve(self.local_knowledge_dir)

    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "extracted"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    def missing_for(self, *groups: str) -> list[str]:
        """Return names of required-but-empty variables for the given feature groups."""
        needs = {
            "api": ["rag_service_secret", "mongo_uri"],
            "mongo": ["mongo_uri"],
            "gemini": ["gemini_api_key"],
            "drive": ["drive_root_folder_id"],
            "local": ["local_knowledge_dir"],
        }
        missing: list[str] = []
        for g in groups:
            for name in needs[g]:
                if not getattr(self, name):
                    missing.append(name.upper())
        return sorted(set(missing))


@lru_cache
def get_settings() -> Settings:
    return Settings()
