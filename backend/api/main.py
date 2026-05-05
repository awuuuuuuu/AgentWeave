from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import logging

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.auth import router as auth_router
from api.routes.chat import router as chat_router
from api.routes.knowledge import router as kb_router
from api.routes.tools import router as tools_router
from api.settings import APISettings
from db.session import Base, engine
import db.models  # noqa: F401  确保所有模型已注册
from rag.chain import RAGChain
from rag.settings import RAGChainSettings
from ingestion.embedder.openai_embedder import OpenAIEmbedder
from ingestion.store.milvus_store import MilvusStoreConfig
from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
from retrieval.reranker import Reranker, RerankerConfig
from agent.tools.tool_registry import ToolRegistry
from agent.tools.tool_executor import ToolExecutor
from agent.tools.builtin import KBSearchTool, WebSearchTool, CalculatorTool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表（幂等）、预热 RAGChain 和检索组件，关闭时自动释放"""
    logger.info("Startup: initializing database...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Startup: initializing RAG components...")
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

    # ── 工具体系初始化 ────────────────────────────────────────────────────────
    logger.info("Startup: initializing tool registry...")
    tool_settings = APISettings()
    registry = ToolRegistry()
    registry.register(KBSearchTool(retriever=app.state.retriever, reranker=app.state.reranker))
    registry.register(CalculatorTool())
    if tool_settings.tavily_api_key:
        registry.register(WebSearchTool(api_key=tool_settings.tavily_api_key))
        logger.info("Startup: WebSearchTool registered (Tavily)")
    else:
        logger.warning("Startup: TAVILY_API_KEY not set, WebSearchTool skipped")
    app.state.tool_registry = registry

    redis_client = None
    if tool_settings.tool_cache_redis_url:
        import redis.asyncio as aioredis
        redis_client = aioredis.from_url(tool_settings.tool_cache_redis_url, decode_responses=True)
        logger.info("Startup: tool cache Redis connected (DB=2)")
    app.state.tool_executor = ToolExecutor(registry=registry, redis_client=redis_client)

    logger.info("Startup complete. Tools: %s", [t.name for t in registry.list_tools()])
    yield
    logger.info("Shutdown: releasing resources...")
    if redis_client is not None:
        await redis_client.aclose()


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
app.include_router(tools_router)

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}