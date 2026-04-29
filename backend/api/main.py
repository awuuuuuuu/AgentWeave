from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.auth import router as auth_router
from api.routes.chat import router as chat_router
from api.routes.knowledge import router as kb_router
from api.settings import APISettings
from rag.chain import RAGChain

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时预热 RAGChain (建立 Milvus 连接、编译 LangGraph), 关闭时自动释放"""
    app.state.rag_chain = RAGChain.from_settings()
    yield
    # 当前无需显式清理；后续引入连接池时在此关闭


_settings = APISettings()

app = FastAPI(title="RAGent API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(kb_router)
app.include_router(chat_router)

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}