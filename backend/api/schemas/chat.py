from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    knowledge_base_id: str = Field(..., min_length=1, max_length=200)
    top_k: int = Field(default=5, ge=1, le=20)

class Citation(BaseModel):
    ref: int
    source_file: str
    section_path: str
    chunk_id: str

class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]