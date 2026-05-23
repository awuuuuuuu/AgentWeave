from __future__ import annotations

from contextlib import asynccontextmanager

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

# config.py 在模块级调用 load_dotenv，此处无需重复
from config import settings

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.auth import router as auth_router
from api.routes.chat import router as chat_router
from api.routes.knowledge import router as kb_router
from api.routes.tools import router as tools_router
from api.routes.agent import router as agent_router
from api.routes.sessions import router as sessions_router
from api.routes.orgs import router as orgs_router
from db.session import Base, engine
import db.models  # noqa: F401  确保所有模型已注册
from rag.chain import RAGChain
from ingestion.embedder.openai_embedder import OpenAIEmbedder
from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig
from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
from retrieval.reranker import Reranker, RerankerConfig
from agent.tools.tool_registry import ToolRegistry
from agent.tools.tool_executor import ToolExecutor
from agent.tools.builtin import KBSearchTool, WebSearchTool, CalculatorTool
from agent.memory.short_term import ShortTermMemory
from agent.memory.long_term import LongTermMemory
from agent.memory.user_profile import UserProfileManager
from agent.memory.memory_manager import MemoryManager
from agent.graph.agent_graph import build_agent_graph
from agent.graph.weave_supervisor import build_weave_graph

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表（幂等）、预热 RAGChain 和检索组件，关闭时自动释放"""
    logger.info("Startup: skipping create_all — schema managed by Alembic migrations")

    # ── RAG 检索组件 ──────────────────────────────────────────────────────────
    logger.info("Startup: initializing RAG components...")
    store_cfg = MilvusStoreConfig(uri=settings.milvus_uri)
    app.state.milvus_store = MilvusStore(store_cfg)
    embedder = OpenAIEmbedder()
    app.state.retriever = HybridRetriever(
        embedder=embedder,
        config=HybridRetrieverConfig(
            store_config=store_cfg,
            candidate_multiplier=settings.candidate_multiplier,
        ),
    )
    app.state.reranker = Reranker(RerankerConfig(
        reranker_type=settings.reranker_type,
        model_name=settings.reranker_model,
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
    )) if settings.use_reranker else None
    app.state.rag_chain = RAGChain.from_components(
        retriever=app.state.retriever,
        reranker=app.state.reranker,
    )

    # ── 工具体系 ──────────────────────────────────────────────────────────────
    logger.info("Startup: initializing tool registry...")
    registry = ToolRegistry()
    registry.register(KBSearchTool(retriever=app.state.retriever, reranker=app.state.reranker))
    registry.register(CalculatorTool())
    if settings.tavily_api_key:
        registry.register(WebSearchTool(api_key=settings.tavily_api_key))
        logger.info("Startup: WebSearchTool registered (Tavily)")
    else:
        logger.warning("Startup: TAVILY_API_KEY not set, WebSearchTool skipped")
    app.state.tool_registry = registry

    redis_client = None
    if settings.tool_cache_redis_url:
        import redis.asyncio as aioredis
        redis_client = aioredis.from_url(settings.tool_cache_redis_url, decode_responses=True)
        logger.info("Startup: tool cache Redis connected (DB=2)")
    app.state.tool_executor = ToolExecutor(registry=registry, redis_client=redis_client)
    logger.info("Startup: tools ready: %s", [t.name for t in registry.list_tools()])

    # ── 记忆系统 + Agent 主图（共享 AsyncPostgresSaver）─────────────────────────
    logger.info("Startup: initializing memory system + agent graph...")
    long_term = LongTermMemory(milvus_uri=settings.milvus_uri)
    long_term.set_embedder(embedder)

    memory_redis = None
    if settings.memory_redis_url:
        import redis.asyncio as aioredis  # noqa: F811
        memory_redis = aioredis.from_url(settings.memory_redis_url, decode_responses=True)
        logger.info("Startup: memory profile cache Redis connected")
    elif redis_client is not None:
        memory_redis = redis_client

    user_profile = UserProfileManager(
        llm_model=settings.memory_llm_model,
        redis_client=memory_redis,
    )

    pg_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

    # 使用连接池代替单一持久连接：
    #   min_size=0  → 空闲时不保留连接，避免 Windows TCP 层静默断开（error 10053）
    #   max_idle=60 → 连接超过 60s 未使用自动释放
    #   keepalives  → 连接 URI 参数，作为双重保障
    _sep = "&" if "?" in pg_url else "?"
    pg_url += f"{_sep}keepalives=1&keepalives_idle=20&keepalives_interval=5&keepalives_count=3"
    async with AsyncConnectionPool(
        conninfo=pg_url,
        min_size=0,
        max_size=5,
        max_idle=60.0,
        open=True,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    ) as pg_pool:
        checkpointer = AsyncPostgresSaver(conn=pg_pool)
        await checkpointer.setup()  # 建 checkpoint 表（幂等）

        # ShortTermMemory 与图共享同一 checkpointer，thread_id 格式对齐
        short_term = ShortTermMemory(
            checkpointer=checkpointer,
            llm_model=settings.memory_llm_model,
        )
        app.state.memory_manager = MemoryManager(
            short_term=short_term,
            long_term=long_term,
            user_profile=user_profile,
        )

        app.state.agent_graph = build_agent_graph(
            retriever=app.state.retriever,
            reranker=app.state.reranker,
            checkpointer=checkpointer,
            memory_manager=app.state.memory_manager,
            llm_model=settings.llm_model,
        )
        app.state.weave_graph = build_weave_graph(checkpointer=checkpointer)
        logger.info("Startup complete.")

        yield

        logger.info("Shutdown: releasing resources...")
        if redis_client is not None:
            await redis_client.aclose()
        if memory_redis is not None and memory_redis is not redis_client:
            await memory_redis.aclose()


app = FastAPI(title="AgentWeave API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(kb_router)
app.include_router(chat_router)
app.include_router(tools_router)
app.include_router(agent_router)
app.include_router(sessions_router)
app.include_router(orgs_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
