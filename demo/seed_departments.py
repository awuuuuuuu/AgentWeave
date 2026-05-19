"""
部门种子脚本（幂等）

创建城市应急响应 Demo 的 5 个部门机构：
  - Organization（含 dept_code + mcp_connections）
  - User（一个主账号）
  - KnowledgeBase（一个知识库）
  - 直接摄入 demo/{dept}/*.md / *.pdf 文档（绕过 Celery/MinIO）
  - 切分策略：ParentChildSplitter（parent=512, child=128）

用法：
    cd backend
    uv run python ../demo/seed_departments.py           # 幂等，跳过已就绪文档
    uv run python ../demo/seed_departments.py --clear   # 清除所有向量+文档记录后重新摄入

前提：
  - 已运行 alembic upgrade head
  - 环境变量已配置（DATABASE_URL / MILVUS_URI / OPENAI_API_KEY 等）
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import string
import sys
from pathlib import Path

# 确保 backend 目录在 sys.path（脚本从项目根目录或 backend 目录运行均可）
_SCRIPT_DIR = Path(__file__).parent
_BACKEND_DIR = _SCRIPT_DIR.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import bcrypt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from config import settings
from db.models import Document, DocumentStatus, KnowledgeBase, Organization, User
from ingestion.embedder.openai_embedder import OpenAIEmbedder
from ingestion.pipeline import IngestionPipeline
from ingestion.splitter.parent_child import ParentChildSplitter, ParentChildConfig
from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig

# ── 部门配置表 ────────────────────────────────────────────────────────────────

DEMO_DIR = _SCRIPT_DIR

DEPARTMENTS = [
    {
        "dept_code": "env_agency",
        "org_name": "环保局",
        "org_type": "department",
        "username": "environment@pku.com",
        "password": "demo1234",
        "kb_name": "环保局应急知识库",
        "doc_dir": DEMO_DIR / "environmental-agency",
        "mcp_connections": [
            {
                "name": "atmospheric_dispersion",
                "url": "http://localhost:8101/mcp",
                "description": "大气扩散模型（只读）：calculate_plume 计算 ERPG-1/2/3 疏散半径；get_evacuation_direction 根据风向给出疏散方向和优先管控路口",
            },
            {
                "name": "enterprise_sensor",
                "url": "http://localhost:8105/mcp",
                "description": "企业传感器（只读）：get_sensor_readings 查询实时读数（sensor_type 可选 氨气浓度/风速/风向/压力/温度）；get_critical_alarms 获取超阈值报警传感器；get_incident_timeline 返回事故时间线",
            },
        ],
        "dept_prompts": {
            "supervisor_hints": "扩散/疏散类问题：先路由 researcher 检索毒性参数和分级标准，再路由 analyst；analyst 必须先调 get_sensor_readings 取风速/风向实测值，再调 calculate_plume 计算扩散半径。",
            "analyst_context": (
                "调用 calculate_plume 前，必须先分两次调用 get_sensor_readings(sensor_type='风速') 和 "
                "get_sensor_readings(sensor_type='风向')，将实测值填入 wind_speed_ms 和 wind_dir_deg 参数，不得使用估算值。"
                "完成这两次读取后，立即调用 calculate_plume 计算扩散半径——不得再进行其他任何 get_sensor_readings 调用，"
                "也不得使用猜测值替代 calculate_plume。"
            ),
        },
    },
    {
        "dept_code": "medical_ems",
        "org_name": "医疗急救(120)",
        "org_type": "department",
        "username": "medical@pku.com",
        "password": "demo1234",
        "kb_name": "医疗急救知识库",
        "doc_dir": DEMO_DIR / "medical-emergency",
        "mcp_connections": [
            {
                "name": "ambulance_dispatch",
                "url": "http://localhost:8102/mcp",
                "description": "救护车调度：list_ambulances 查询车辆状态（可过滤 待命/出车）；get_hospital_capacity 查询医院ICU/急诊容量；dispatch_ambulance⚠️ 派遣救护车（写操作，需HITL）；recall_ambulance⚠️ 召回救护车（写操作，需HITL）",
            },
            {
                "name": "amap",
                "url": "http://localhost:8106/mcp",
                "description": "高德地图：plan_driving_route 规划驾车路线，返回时长/距离/途经坐标点；geocode 将地址字符串转换为经纬度",
            },
        ],
        "dept_prompts": {
            "supervisor_hints": (
                "派车类问题：先路由 researcher 检索接诊级别要求，再路由 analyst 查容量和车辆状态。"
                "【关键】analyst 调用过 get_hospital_capacity 或 list_ambulances 后，派车方案已就绪，"
                "下一步必须路由 hitl——不得路由 analyst（dispatch_ambulance 是写操作，analyst 不可自行执行）、"
                "不得路由 reporter、不得路由 __end__。hitl 通过后再路由 reporter 输出结论。"
                "纯路线规划（只需 plan_driving_route）不涉及写操作，analyst 后直接 reporter 即可。"
                "纯急救规程/设备/药品查询（不含派车、不含路线规划，如'洗消流程''给氧步骤''转运注意事项'等）："
                "只需路由 researcher 检索文档，researcher 完成后直接路由 reporter，无需 analyst。"
            ),
            "analyst_context": (
                "先调 get_hospital_capacity() 确认各医院 ICU 可用容量，再调 list_ambulances(status='待命') 确认可用车辆，"
                "整理派车方案后停止——dispatch_ambulance 是写操作，需等待 HITL 审批，不得自行调用。"
                "纯路线规划任务（只调用了 geocode/plan_driving_route）直接输出路线结果即可。"
            ),
        },
    },
    {
        "dept_code": "traffic_control",
        "org_name": "交通管控",
        "org_type": "department",
        "username": "traffic@pku.com",
        "password": "demo1234",
        "kb_name": "交通管制知识库",
        "doc_dir": DEMO_DIR / "traffic-control",
        "mcp_connections": [
            {
                "name": "signal_control",
                "url": "http://localhost:8103/mcp",
                "description": "交通信号控制：list_intersections 查询8个路口当前模式；apply_evacuation_plan⚠️ 按预案等级批量设置路口（Ⅳ/Ⅲ/Ⅱ级，写操作，需HITL）；set_mode⚠️ 设置单个路口模式（写操作，需HITL）",
            },
            {
                "name": "amap",
                "url": "http://localhost:8106/mcp",
                "description": "高德地图：plan_driving_route 规划驾车路线，返回时长/距离/途经坐标点；geocode 将地址字符串转换为经纬度",
            },
        ],
        "dept_prompts": {
            "supervisor_hints": (
                "管控类问题：先路由 researcher 检索预案等级定义，再路由 analyst 查路口现状并整理变更方案。"
                "【关键】analyst 调用过 list_intersections 后，路口方案已就绪，"
                "下一步必须路由 hitl——apply_evacuation_plan 和 set_mode 是写操作，"
                "不得路由 reporter 或 __end__，即使 researcher 和 analyst 均已完成也不例外。"
                "路线+信号联合任务（如救援走廊）：analyst 完成路线规划和路口识别后，同样必须路由 hitl 审批信号设置，再路由 reporter。"
            ),
            "analyst_context": (
                "【绝对禁令】apply_evacuation_plan 和 set_mode 是写操作，在 HITL 审批前严禁调用，"
                "即使 supervisor 指令要求'设置'或'批量管控'也不得执行——analyst 只能读取状态并输出文字方案。"
                "\n【路线+信号联合任务】先用 geocode() 获取起点和终点坐标，调用 plan_driving_route() 规划路线，"
                "然后调用 list_intersections() 获取沿线路口现状，整理需要切换为应急绿波的路口列表后停止，"
                "在回复末尾加上『[需要HITL审批]』标记。"
                "\n【仅路口管控任务】先调 list_intersections() 获取所有路口当前信号模式，整理变更方案后停止，"
                "在回复末尾加上『[需要HITL审批]』标记。"
                "\n【仅路线规划任务】先用 geocode() 分别获取起点和终点坐标，直接调用 plan_driving_route() 完成路线规划，"
                "无需 HITL 标记。"
            ),
        },
    },
    {
        "dept_code": "emergency_supplies",
        "org_name": "应急物资",
        "org_type": "department",
        "username": "supply@pku.com",
        "password": "demo1234",
        "kb_name": "应急物资知识库",
        "doc_dir": DEMO_DIR / "emergency-supplies",
        "mcp_connections": [
            {
                "name": "warehouse",
                "url": "http://localhost:8104/mcp",
                "description": "应急物资仓库：get_inventory 查询库存（可按类别过滤）；check_alerts 查询低于预警线的物资；allocate_standard_pack⚠️ 按预案等级发放标准包（写操作，需HITL）；allocate_custom⚠️ 自定义调拨（写操作，需HITL）",
            },
        ],
        "dept_prompts": {
            "supervisor_hints": (
                "调拨类问题：先路由 researcher 检索标准包定义和最低储备要求，再路由 analyst 查库存告警。"
                "allocate_standard_pack 和 allocate_custom 是写操作，analyst 完成后必须路由 hitl 审批。"
                "告警核查/补充建议类（如'哪些物资低于阈值''给出补充建议'）："
                "先路由 researcher 检索各类物资的额定储量标准和补充触发条件，"
                "再路由 analyst 调用 check_alerts 获取实时告警状态。"
                "analyst 完成后路由 reporter 输出建议，无需 HITL。"
            ),
            "analyst_context": (
                "【第一步】必须先调用 check_alerts()——无论任务是调拨还是核查，都要先获取告警状态，不可跳过。"
                "【第二步-调拨任务】任务含'调拨''出库''发放'等字眼时，check_alerts 之后必须继续调用 get_inventory() "
                "核对标准包所需物资的实际库存数量，即使 check_alerts 返回无告警也不可跳过——无告警不等于库存充足。"
                "【第二步-纯告警核查】问题只问'哪些物资低于阈值'时，check_alerts 结果已足够，无需再调 get_inventory。"
                "整理完方案后停止——allocate_standard_pack 和 allocate_custom 是写操作，需等待 HITL 审批，不得自行调用。"
            ),
        },
    },
    {
        "dept_code": "enterprise_safety",
        "org_name": "企业安全",
        "org_type": "department",
        "username": "safety@pku.com",
        "password": "demo1234",
        "kb_name": "企业安全知识库",
        "doc_dir": DEMO_DIR / "enterprise-safety",
        "mcp_connections": [
            {
                "name": "enterprise_sensor",
                "url": "http://localhost:8105/mcp",
                "description": "企业传感器（只读）：get_sensor_readings 查询实时读数（sensor_type 可选 氨气浓度/风速/风向/压力/温度）；get_critical_alarms 获取超阈值报警传感器；get_incident_timeline 返回事故时间线",
            },
        ],
        "dept_prompts": {
            "supervisor_hints": (
                "根因分析类（含'根本原因''诱因''原因分析'等关键词）："
                "先路由 researcher 检索事故快报和设备安全数据，再路由 analyst 获取实时告警和时间线，最后 reporter 综合分析。"
                "处置方法/防护规程类（含'处置方案''防护装备''处置方法'且无实时查询需求）："
                "直接路由 researcher 检索处置规程，完成后路由 reporter，无需 analyst。"
                "纯传感器/时间线查询（含'梳理时间线''查超限读数''超过阈值'且无需查文档）："
                "直接路由 analyst，完成后路由 reporter，无需 researcher。"
                "所有传感器工具只读，无需 HITL。"
            ),
            "analyst_context": "所有工具只读，可直接调用无需 HITL。根因分析时同时调 get_incident_timeline() 和 get_critical_alarms()；分析压力异常时同时查 sensor_type='压力' 和 sensor_type='温度' 做关联分析。",
        },
    },
]


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _gen_invite_code(length: int = 8) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def _collect_docs(doc_dir: Path) -> list[Path]:
    if not doc_dir.exists():
        return []
    files = []
    for pattern in ("*.md", "*.pdf"):
        files.extend(doc_dir.glob(pattern))
    return files


# ── 核心逻辑 ──────────────────────────────────────────────────────────────────

async def _setup_dept(
    sf: async_sessionmaker, dept: dict
) -> tuple[str, str]:
    """
    Phase 1（短 session）：幂等创建机构/用户/知识库，返回 (kb_id, user_id)。
    commit 后立即释放连接，避免长期持有。
    """
    dept_code = dept["dept_code"]
    async with sf() as db:
        # 机构
        org = await db.scalar(
            select(Organization).where(Organization.dept_code == dept_code)
        )
        if org is None:
            org = Organization(
                name=dept["org_name"],
                type=dept["org_type"],
                dept_code=dept_code,
                invite_code=_gen_invite_code(),
                mcp_connections=dept["mcp_connections"],
                dept_prompts=dept.get("dept_prompts"),
            )
            db.add(org)
            await db.flush()
            print(f"[创建] 机构: {dept['org_name']} ({dept_code})")
        else:
            org.mcp_connections = dept["mcp_connections"]
            if "dept_prompts" in dept:
                org.dept_prompts = dept["dept_prompts"]
            print(f"[跳过] 机构已存在: {dept['org_name']} — 更新 mcp_connections + dept_prompts")

        # 用户
        user = await db.scalar(select(User).where(User.email == dept["username"]))
        if user is None:
            user = User(
                email=dept["username"],
                hashed_password=_hash_password(dept["password"]),
                org_id=org.id,
            )
            db.add(user)
            await db.flush()
            print(f"  [创建] 用户: {dept['username']}")
        else:
            if user.org_id != org.id:
                user.org_id = org.id
            print(f"  [跳过] 用户已存在: {dept['username']}")

        # 知识库
        kb = await db.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.user_id == user.id,
                KnowledgeBase.name == dept["kb_name"],
                KnowledgeBase.is_deleted.is_(False),
            )
        )
        if kb is None:
            kb = KnowledgeBase(name=dept["kb_name"], user_id=user.id, org_id=org.id)
            db.add(kb)
            await db.flush()
            print(f"  [创建] 知识库: {dept['kb_name']} (kb_id={kb.id})")
        else:
            print(f"  [跳过] 知识库已存在: {dept['kb_name']} (kb_id={kb.id})")

        kb_id = kb.id
        await db.commit()  # ← 立即释放连接

    return kb_id


async def _ingest_doc(
    sf: async_sessionmaker,
    pipeline: IngestionPipeline,
    kb_id: str,
    doc_path: Path,
) -> None:
    """
    Phase 2：单文档摄入。
    - 短 session 创建 Document 记录后立即 commit 释放连接
    - asyncio.to_thread 期间不持有任何 DB 连接（可能耗时数分钟）
    - 完成后再开短 session 更新状态
    """
    # 检查是否已 ready
    async with sf() as db:
        existing = await db.scalar(
            select(Document).where(
                Document.kb_id == kb_id,
                Document.filename == doc_path.name,
                Document.status == DocumentStatus.READY.value,
                Document.is_deleted.is_(False),
            )
        )
        if existing:
            print(f"  [跳过] 文档已就绪: {doc_path.name}")
            return

        doc = Document(kb_id=kb_id, filename=doc_path.name, status=DocumentStatus.PROCESSING.value)
        db.add(doc)
        await db.flush()
        doc_id = doc.id
        await db.commit()  # ← 释放连接，后面是长时间摄入

    # 长时间摄入，不持有 DB 连接
    print(f"  [摄入] {doc_path.name} → kb_id={kb_id} ...")
    result = await asyncio.to_thread(pipeline.run, [doc_path], kb_id, doc_path.name, doc_id)

    # 写回状态，再开新 session
    async with sf() as db:
        doc = await db.get(Document, doc_id)
        if result.succeeded == 1:
            doc.status = DocumentStatus.READY.value
            print(f"  [完成] {doc_path.name}: {result.total_chunks_written} 个 chunk")
        else:
            error_msg = result.errors.get(doc_path.name, "未知错误")
            doc.status = DocumentStatus.ERROR.value
            doc.error_message = error_msg
            print(f"  [失败] {doc_path.name}: {error_msg}")
        await db.commit()


async def _clear_kb(sf: async_sessionmaker, store: MilvusStore, kb_id: str) -> None:
    """清除知识库的所有 Milvus 向量 + PostgreSQL 文档记录（供 --clear 使用）。"""
    print(f"  [清除] 删除 Milvus 向量 kb_id={kb_id} ...")
    await asyncio.to_thread(store.delete_by_kb, kb_id)

    async with sf() as db:
        result = await db.execute(
            delete(Document).where(Document.kb_id == kb_id)
        )
        await db.commit()
    print(f"  [清除] 已删除 {result.rowcount} 条文档记录")


async def seed(
    sf: async_sessionmaker,
    pipeline: IngestionPipeline,
    store: MilvusStore,
    clear: bool = False,
) -> None:
    for dept in DEPARTMENTS:
        kb_id = await _setup_dept(sf, dept)

        if clear:
            await _clear_kb(sf, store, kb_id)

        doc_files = _collect_docs(dept["doc_dir"])
        if not doc_files:
            print(f"  [警告] 未找到文档: {dept['doc_dir']}")
        else:
            for doc_path in doc_files:
                await _ingest_doc(sf, pipeline, kb_id, doc_path)

        print()

    print("=== 种子数据写入完成 ===")


async def main() -> None:
    parser = argparse.ArgumentParser(description="部门种子脚本")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="清除各知识库现有向量数据和文档记录后重新摄入",
    )
    args = parser.parse_args()

    if args.clear:
        print("⚠️  --clear 模式：将清除所有知识库数据后重新摄入\n")

    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=2,
        connect_args={"ssl": False},
    )
    sf = async_sessionmaker(engine, expire_on_commit=False)

    store_cfg = MilvusStoreConfig(uri=settings.milvus_uri)
    embedder = OpenAIEmbedder()
    store = MilvusStore(store_cfg)
    splitter = ParentChildSplitter(ParentChildConfig(
        parent_chunk_size=512,
        child_chunk_size=128,
        child_overlap=16,
    ))
    pipeline = IngestionPipeline(embedder=embedder, store=store, splitter=splitter)

    await seed(sf, pipeline, store, clear=args.clear)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
