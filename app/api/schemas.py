from typing import Any, Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10)
    branch: Optional[str] = Field(default=None, max_length=50)
    semester: Optional[int] = Field(default=None, ge=1, le=10)
    subject: Optional[str] = Field(default=None, max_length=100)
    academic_year: Optional[str] = Field(default=None, max_length=20)
    doc_type: Optional[str] = Field(default=None, max_length=50)
    history: list[str] = Field(default_factory=list, max_length=6,
                               description="Recent user messages, used only to rewrite follow-up queries")


class SourceChunk(BaseModel):
    chunk_id: str
    text: str
    document_id: str
    document_name: str
    page_number: Optional[int] = None
    source_url: Optional[str] = None
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    rewritten_query: str
    count: int
    results: list[SourceChunk]
    retrieval: dict[str, Any] = Field(default_factory=dict)


class DocumentOut(BaseModel):
    document_id: str
    file_id: str
    file_name: str
    folder_path: str
    mime_type: str
    status: str
    approved: bool
    chunk_count: int = 0
    error: Optional[str] = None
    updated_at: Optional[str] = None
    source_url: Optional[str] = None
    source_type: Optional[str] = None
    source_path: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentsResponse(BaseModel):
    total: int
    items: list[DocumentOut]


class ApprovalRequest(BaseModel):
    approved: bool


class StatsResponse(BaseModel):
    total_documents: int
    indexed_documents: int
    failed_documents: int
    pending_documents: int
    deleted_documents: int
    approved_documents: int
    disabled_documents: int
    total_chunks: int
    embedding_model: Optional[str] = None
    embedding_dimensions: Optional[int] = None
    last_sync: Optional[dict[str, Any]] = None
