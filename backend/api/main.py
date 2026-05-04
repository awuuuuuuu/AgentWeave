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
from db.session import Base, engine
import db.models  # noqa: F401  确保所有模型已注册
from rag.chain import RAGChain
from rag.settings import RAGChainSettings
from ingestion.embedder.openai_embedder import OpenAIEmbedder
from ingestion.store.milvus_store import MilvusStoreConfig
from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
from retrieval.reranker import Reranker, RerankerConfig

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表（幂等）、预热 RAGChain 和检索组件，关闭时自动释放"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    cfg = RAGChainSettings()
    store_cfg = MilvusStoreConfig(uri=cfg.milvus_uri)
    embedder = OpenAIEmbedder()
    app.state.retriever = HybridRetriever(
        embedder=embedder,
        config=HybridRetrieverConfig(store_config=store_cfg, candidate_multiplier=cfg.candidate_multiplier),
    )
    app.state.reranker = Reranker(RerankerConfig(
        reranker_type=cfg.reranker_type,
        model_name=cfg.reranker_model,
        api_key=cfg.dashscope_api_key,
        base_url=cfg.dashscope_base_url,
    )) if cfg.use_reranker else None
    app.state.rag_chain = RAGChain.from_components(
        retriever=app.state.retriever,
        reranker=app.state.reranker,
        settings=cfg,
    )
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