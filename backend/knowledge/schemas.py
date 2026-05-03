from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from db.models import DocumentStatus

class KBCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None

class KBUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None

class KBRetrievalSettings(BaseModel):
    """检索设置，单独 PATCH 用。"""
    retrieval_mode: str = Field(default="hybrid", pattern="^(vector|fulltext|hybrid)$")
    use_rerank: bool = True
    top_k: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    hybrid_mode: str = Field(default="weighted", pattern="^(weighted|rerank)$")
    vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)

class KBResponse(BaseModel):
    id: str
    name: str
    description: str | None
    user_id: str
    retrieval_mode: str
    use_rerank: bool
    top_k: int
    score_threshold: float
    hybrid_mode: str
    vector_weight: float
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class DocumentResponse(BaseModel):
    id: str
    kb_id: str
    filename: str
    status: DocumentStatus
    error_message: str | None
    task_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class UploadResponse(BaseModel):
    document_id: str
    task_id: str
    filename: str
    status: DocumentStatus

class ChunkPreviewItem(BaseModel):
    index: int
    text: str
    content_type: str
    page_number: int | None
    section_path: str
    token_count: int