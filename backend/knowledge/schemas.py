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

class KBResponse(BaseModel):
    id: str
    name: str
    description: str | None
    user_id: str
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