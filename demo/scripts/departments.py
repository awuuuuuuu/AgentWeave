"""部门种子脚本：幂等创建机构 / 用户 / 知识库并摄入文档。

运行（需在 backend/ 目录下执行）：
    cd backend
    uv run python ../demo/scripts/departments.py
    uv run python ../demo/scripts/departments.py --clear
    uv run python ../demo/scripts/departments.py --only fire_brigade
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import string
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent
_DEMO_DIR    = _SCRIPTS_DIR.parent
_BACKEND_DIR = _DEMO_DIR.parent / "backend"

# ── 部门配置 ──────────────────────────────────────────────────────────────────
DEPARTMENTS = [
    {
        "dept_code": "cmd_center",
        "org_name": "应急指挥中心",
        "org_type": "command",
        "username": "coordinator@pku.com",
        "password": "demo1234",
        "kb_name": "应急指挥中心知识库",
        "doc_dir": _DEMO_DIR / "cmd-center",
        "mcp_connections": [],
        "dept_prompts": {
            "supervisor_hints": (
                "你是应急指挥中心总协调员（Weave Orchestrator），负责接收事故报告并协调下属五个部门同步响应。\n"
                "【初始研判】先路由 researcher 检索匹配的应急预案等级与启动条件；同步路由 analyst 通过传感器确认现场态势（浓度/风向/告警级别）。\n"
                "【分派原则】根据研判结果，按如下专责将子任务分派给相应部门（在上层 Weave 中体现为路由决策）：\n"
                "  · EN 环保局    ──── 大气扩散建模 + 疏散方向\n"
                "  · ME 医疗急救  ──── 伤员接诊 + 救护车调度（含 HITL）\n"
                "  · TR 交通管控  ──── 路口信号管制 + 疏散通道（含 HITL）\n"
                "  · LG 应急物资  ──── 防护物资调拨（含 HITL）\n"
                "  · FF 消防救援  ──── 消防资源调度 + 火场警戒\n"
                "【执行原则】涉及写操作（派车/路口信号/物资调拨）的步骤，由 executor 节点在 HITL 审批后自动执行，无需部门 analyst 主动调用写操作。\n"
                "【汇总简报】所有部门响应完成（或首批结果就绪）后，路由 reporter 生成总指挥简报，"
                "格式：事故概况 → 各部门响应状态 → 待审批项 → 下一步行动建议。"
            ),
            "analyst_context": (
                "你负责初始态势研判（cmd_center 无直接 MCP 工具，依赖子部门上报数据）。\n"
                "整合事故描述与各部门评估结果，输出：\n"
                "- 事故类型与当前建议响应等级（Ⅰ~Ⅳ级）\n"
                "- 已知伤亡与影响范围\n"
                "- 建议优先启动的部门及任务分工\n"
                "- 下一步行动建议（含 HITL 待审批项）"
            ),
        },
    },
    {
        "dept_code": "env_agency",
        "org_name": "环保局",
        "org_type": "department",
        "a2a_url": "http://localhost:9101",
        "username": "environment@pku.com",
        "password": "demo1234",
        "kb_name": "环保局应急知识库",
        "doc_dir": _DEMO_DIR / "environmental-agency",
        "mcp_connections": [
            {"name": "environment_sensor", "url": "http://localhost:8105/mcp",
             "description": "通用环境传感器（只读）：get_sensor_readings 查询实时读数（sensor_type 可选 风速/风向/温度/烟雾/一氧化碳/PM2.5）；get_critical_alarms 获取超阈值报警传感器"},
            {"name": "amap", "url": "http://localhost:8106/mcp",
             "description": "高德地图：plan_driving_route 规划驾车路线；geocode 地址转坐标"},
        ],
        "dept_prompts": {
            "supervisor_hints": (
                "含环境监测/空气质量/烟雾/传感器/预案/预警/告警/事故定级等环保实务性任务（含或不含'【研判阶段】'）：\n"
                "  先路由 researcher 检索环保应急规程（current_task 须明确写出检索目标，如「上海市环境应急预案空气质量预警级别与响应措施」，禁止使用'相关内容'等泛化表述），\n"
                "  再路由 analyst 调用 get_critical_alarms 和 get_sensor_readings 获取实时环境数据，\n"
                "  路由 reporter 输出环境评估报告。\n"
            ),
            "analyst_context": (
                "【环境监测任务】依次调用：\n"
                "1. get_critical_alarms() — 确认当前超阈值告警传感器\n"
                "2. get_sensor_readings(sensor_type='烟雾') — 获取烟雾浓度\n"
                "3. get_sensor_readings(sensor_type='风速') — 获取风速\n"
                "4. get_sensor_readings(sensor_type='风向') — 获取风向（度数，0°=正北，90°=正东）\n"
                "5. get_sensor_readings(sensor_type='一氧化碳') — 获取 CO 浓度\n"
                "根据风向度数推断扩散方向（如 180° 为南风，污染物向北扩散）。\n"
                "【扩散范围评估】给出定性估算，明确标注「以下为基于传感器数据的估算，非精确模型计算结果」。\n"
                "【MCP引用标注】引用数据后加[M数字]，如「烟雾浓度68%[M2]，CO超标62ppm[M5]」"
            ),
        },
    },
    {
        "dept_code": "medical_ems",
        "org_name": "医疗急救(120)",
        "org_type": "department",
        "a2a_url": "http://localhost:9102",
        "username": "medical@pku.com",
        "password": "demo1234",
        "kb_name": "医疗急救知识库",
        "doc_dir": _DEMO_DIR / "medical-emergency",
        "mcp_connections": [
            {"name": "ambulance_dispatch", "url": "http://localhost:8102/mcp",
             "description": "救护车调度：list_ambulances 查询车辆状态（可过滤 待命/出车）；get_hospital_capacity 查询医院ICU/急诊容量；dispatch_ambulance⚠️ 派遣救护车（写操作，需HITL）；recall_ambulance⚠️ 召回救护车（写操作，需HITL）"},
            {"name": "amap", "url": "http://localhost:8106/mcp",
             "description": "高德地图：plan_driving_route 规划驾车路线，返回时长/距离/途经坐标点；geocode 将地址字符串转换为经纬度"},
        ],
        "dept_prompts": {
            "supervisor_hints": (
                "含急救规程/接诊/救护/医院/救护车/床位等实务性任务（含或不含'【研判阶段】'）：\n"
                "  先路由 researcher，无论知识库是否有结果，此步完成后必须继续到 analyst 调用 get_hospital_capacity 和 list_ambulances；禁止 researcher 完成后直接路由 reporter。\n"
                "  若任务含派遣/调度/派车/dispatch 请求，analyst 完成后路由 hitl 审批，再路由 reporter；否则直接 reporter 输出建议。\n"
                "纯路线规划/坐标查询任务：直接路由 analyst，完成后 __end__，无需 reporter 也无需 HITL。"
            ),
            "analyst_context": (
                "【研判阶段】依次调用：\n"
                "1. get_hospital_capacity() — 查询各医院 ICU 和急诊可用容量\n"
                "2. list_ambulances(status='待命') — 获取当前待命救护车数量和位置\n"
                "整理评估方案后停止，勿调用 dispatch_ambulance/recall_ambulance（写操作，由 executor 执行）。\n"
                "若任务明确要求立即派遣救护车，系统会阻止写操作调用并由你输出：\n"
                "【HITL_REQUIRED】待执行：dispatch_ambulance\n"
                "【EXECUTION_INTENT】{\"tool_name\": \"dispatch_ambulance\", \"params\": {\"ambulance_id\": \"auto\", \"dest_lat\": <事故点纬度 float>, \"dest_lng\": <事故点经度 float>, \"patient_type\": \"<伤员类型>\"}}\n"
                "无法确定的参数填 \"auto\"，executor 会在运行时查询补全。\n"
                "【geocode 地址规范】调用 geocode 时必须补全城市前缀，例如：'东方医院' → '上海市浦东新区东方医院'。\n"
                "【MCP引用标注】引用实时数据加[M数字]，如「东方医院ICU可用12张[M1]，A3救护车待命[M2]」"
            ),
        },
    },
    {
        "dept_code": "traffic_control",
        "org_name": "交通管控",
        "org_type": "department",
        "a2a_url": "http://localhost:9103",
        "username": "traffic@pku.com",
        "password": "demo1234",
        "kb_name": "交通管制知识库",
        "doc_dir": _DEMO_DIR / "traffic-control",
        "mcp_connections": [
            {"name": "signal_control", "url": "http://localhost:8103/mcp",
             "description": "交通信号控制：list_intersections 查询路口当前模式；apply_evacuation_plan⚠️ 按预案等级批量设置路口（写操作，需HITL）；set_mode⚠️ 设置单个路口模式（写操作，需HITL）"},
            {"name": "amap", "url": "http://localhost:8106/mcp",
             "description": "高德地图：plan_driving_route 规划驾车路线；geocode 将地址字符串转换为经纬度"},
        ],
        "dept_prompts": {
            "supervisor_hints": (
                "含预案等级/路口管控/疏散/信号等交通管控任务（含或不含'【研判阶段】'）：先路由 researcher，"
                "无论知识库是否有结果，此步完成后必须继续到 analyst 调用 list_intersections；禁止 researcher 完成后直接路由 reporter。"
                "若任务含信号切换/批量设置等写操作请求，analyst 完成后路由 hitl 审批；否则路由 reporter 输出方案。\n"
                "含路线规划+路口信号设置联合任务：路由 analyst 时 current_task 一次性包含「规划路线、查询路口状态、尝试切换信号」；再路由 hitl，审批后路由 reporter。\n"
                "纯路线规划/坐标查询：analyst 完成后直接 __end__，无需 reporter 也无需 HITL。"
            ),
            "analyst_context": (
                "【路口管控任务】先调 list_intersections() 获取所有路口当前信号模式。\n"
                "【写操作请求（含「批量设置」「切换信号」「apply_evacuation_plan」「set_mode」等）】：\n"
                "① list_intersections 查询路口状态 → ② 尝试调用写操作工具（系统会阻止）→\n"
                "③ 输出：【HITL_REQUIRED】待执行：<工具名>\n"
                "         【EXECUTION_INTENT】{\"tool_name\": \"<工具名>\", \"params\": {\"intersection_id\": \"auto\", \"mode\": \"<模式>\"}}\n"
                "无法确定的参数填 \"auto\"，executor 会在运行时查询补全。\n"
                "【路线+信号联合任务】在一次调用中完成：geocode → plan_driving_route → list_intersections → （触发 HITL_REQUIRED）。\n"
                "【geocode 地址规范】调用时必须补全城市前缀，例如：'世纪大道' → '上海市浦东新区世纪大道'。\n"
                "【MCP引用标注】引用数据加[M数字]，如「S3路口当前正常模式[M1]，疏散路线全长2.3km[M2]」"
            ),
        },
    },
    {
        "dept_code": "emergency_supplies",
        "org_name": "应急物资",
        "org_type": "department",
        "a2a_url": "http://localhost:9104",
        "username": "supply@pku.com",
        "password": "demo1234",
        "kb_name": "应急物资知识库",
        "doc_dir": _DEMO_DIR / "emergency-supplies",
        "mcp_connections": [
            {"name": "warehouse", "url": "http://localhost:8104/mcp",
             "description": "应急物资仓库：get_inventory 查询库存（可按类别过滤）；check_alerts 查询低于预警线的物资；allocate_standard_pack⚠️ 按预案等级发放标准包（写操作，需HITL）；allocate_custom⚠️ 自定义调拨（写操作，需HITL）"},
            {"name": "amap", "url": "http://localhost:8106/mcp",
             "description": "高德地图：plan_driving_route 规划驾车路线；geocode 将地址字符串转换为经纬度"},
        ],
        "dept_prompts": {
            "supervisor_hints": (
                "含物资/调拨/库存/规程/标准包等实务性任务（含或不含'【研判阶段】'）：先路由 researcher，"
                "无论知识库是否有结果，此步完成后必须继续到 analyst 调用 get_inventory 和 check_alerts；禁止 researcher 完成后直接路由 reporter。"
                "若任务含调拨出库请求，analyst 完成后路由 hitl 审批；否则 reporter 输出评估。\n"
                "其他查询：先路由 researcher，此步完成后必须继续到 analyst 调用 check_alerts；reporter 整合输出；无需 HITL。"
            ),
            "analyst_context": (
                "【研判阶段】依次调用：\n"
                "1. get_inventory() — 查询全库存清单（含仓库坐标，供运输路线使用）\n"
                "2. check_alerts() — 查询当前低于预警线的物资\n"
                "整理评估方案后停止，勿调用 allocate_*（写操作，由 executor 执行）。\n"
                "若任务明确要求调拨出库，系统会阻止写操作调用并由你输出：\n"
                "【HITL_REQUIRED】待执行：allocate_standard_pack\n"
                "【EXECUTION_INTENT】{\"tool_name\": \"allocate_standard_pack\", \"params\": {\"level\": \"Ⅲ\"}}\n"
                "无法确定的参数填 \"auto\"，executor 会在运行时查询补全。\n"
                "【MCP引用标注】引用数据加[M数字]，如「防护口罩N95库存200个[M1]，告警物资2项[M2]」"
            ),
        },
    },
    {
        "dept_code": "fire_brigade",
        "org_name": "消防救援",
        "org_type": "department",
        "a2a_url": "http://localhost:9105",
        "username": "fire@pku.com",
        "password": "demo1234",
        "kb_name": "消防救援知识库",
        "doc_dir": _DEMO_DIR / "fire-brigade",
        "mcp_connections": [
            {"name": "fire_station", "url": "http://localhost:8107/mcp",
             "description": "消防救援：get_fire_stations 查询附近消防站和可用车辆数；get_water_supplies 查询周边消防水源；set_fire_perimeter 设置火场警戒圈（只读，无需HITL）；dispatch_fire_trucks⚠️ 调派消防车（写操作，需HITL）；recall_fire_trucks⚠️ 回撤消防车（写操作，需HITL）"},
            {"name": "amap", "url": "http://localhost:8106/mcp",
             "description": "高德地图：plan_driving_route 规划驾车路线；geocode 地址转坐标"},
        ],
        "dept_prompts": {
            "supervisor_hints": (
                "【纯评估/查询任务】无明确调派/撤回动作：先路由 researcher，再路由 analyst 调用 get_fire_stations 和 get_water_supplies；analyst 完成后直接路由 reporter，无需 HITL。\n"
                "【含写操作请求任务】含「调派消防车」「撤回消防车」等动词：必须先路由 analyst，analyst 尝试调用写操作后输出 HITL_REQUIRED，再路由 hitl 审批，审批通过后路由 reporter。\n"
                "警戒圈设置（set_fire_perimeter）：路由 analyst 执行后直接路由 reporter，无需 HITL。"
            ),
            "analyst_context": (
                "【情况一：任务含「调派消防车」关键词】\n"
                "步骤1: 直接调用 dispatch_fire_trucks（station_id 填 auto，系统会阻止此写操作，这是预期行为）\n"
                "步骤2: 收到阻止后立即输出（不得省略）：\n"
                "【HITL_REQUIRED】待执行：dispatch_fire_trucks\n"
                "【EXECUTION_INTENT】{\"tool_name\": \"dispatch_fire_trucks\", \"params\": {\"station_id\": \"auto\", \"truck_count\": <数量 int>, \"dest_lat\": <事故纬度 float>, \"dest_lng\": <事故经度 float>}}\n"
                "\n"
                "【情况二：任务含「撤回消防车」关键词】\n"
                "步骤1: 直接调用 recall_fire_trucks（无需先查询消防站，station_id 填 auto；系统会阻止此写操作，这是预期行为）\n"
                "步骤2: 收到阻止后立即输出（不得省略）：\n"
                "【HITL_REQUIRED】待执行：recall_fire_trucks\n"
                "【EXECUTION_INTENT】{\"tool_name\": \"recall_fire_trucks\", \"params\": {\"station_id\": \"auto\", \"truck_count\": 2}}\n"
                "\n"
                "【情况三：纯研判/查询（不含调派/撤回）】\n"
                "步骤1: get_fire_stations() — 查询消防站及可用车辆数量\n"
                "步骤2: get_water_supplies() — 查询消防水源\n"
                "整理推荐调派方案后停止，禁止调用 dispatch/recall。\n"
                "\n"
                "【set_fire_perimeter】：直接调用，无需 HITL（只读标注）。\n"
                "【MCP引用标注】引用数据加[M数字]，如「陆家嘴站可用4辆[M1]，水池距事故点1.2km[M2]」"
            ),
        },
    },
]


async def _check_deps() -> None:
    """PostgreSQL / Milvus 可达性预检，不可达时立即报错并给出解决提示。"""
    for p in [str(_BACKEND_DIR), str(_DEMO_DIR)]:
        if p not in sys.path:
            sys.path.insert(0, p)

    from config import settings  # type: ignore[import]

    errors: list[str] = []

    # ── PostgreSQL ──────────────────────────────────────────────────────
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        _engine = create_async_engine(str(settings.database_url), pool_size=1, max_overflow=0)
        async with _engine.connect():
            pass
        await _engine.dispose()
    except Exception as exc:
        errors.append(
            f"❌ PostgreSQL 不可达：{exc}\n"
            "   请确认 DATABASE_URL 配置正确且数据库已启动\n"
            f"   当前值：{getattr(settings, 'database_url', '（未配置）')}"
        )

    # ── Milvus ──────────────────────────────────────────────────────────
    from urllib.parse import urlparse
    _milvus_uri = str(getattr(settings, "milvus_uri", "http://localhost:19530"))
    _parsed = urlparse(_milvus_uri)
    milvus_host = _parsed.hostname or "localhost"
    milvus_port = _parsed.port or 19530
    try:
        _, _w = await asyncio.wait_for(
            asyncio.open_connection(milvus_host, milvus_port), timeout=5.0
        )
        _w.close()
        await _w.wait_closed()
    except Exception as exc:
        errors.append(
            f"❌ Milvus 不可达（{milvus_host}:{milvus_port}）：{exc}\n"
            f"   当前 MILVUS_URI={_milvus_uri}\n"
            "   请确认 Milvus 已启动，或检查 MILVUS_URI 配置"
        )

    if errors:
        raise RuntimeError(
            "departments.py 启动前检查失败，请先解决以下问题：\n\n"
            + "\n\n".join(errors)
        )


async def run(clear: bool = False, only: str | None = None) -> None:
    await _check_deps()

    for p in [str(_BACKEND_DIR), str(_DEMO_DIR)]:
        if p not in sys.path:
            sys.path.insert(0, p)

    import bcrypt
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from config import settings
    from db.models import Document, DocumentStatus, KnowledgeBase, Organization, User
    from ingestion.embedder.openai_embedder import OpenAIEmbedder
    from ingestion.pipeline import IngestionPipeline
    from ingestion.splitter.parent_child import ParentChildSplitter, ParentChildConfig
    from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig

    def _hash(pw):
        return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

    def _invite():
        return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))

    def _docs(doc_dir):
        if not doc_dir.exists():
            return []
        return [f for pat in ("*.md", "*.pdf") for f in doc_dir.glob(pat)]

    async def _setup(sf, dept):
        async with sf() as db:
            org = await db.scalar(select(Organization).where(Organization.dept_code == dept["dept_code"]))
            if org is None:
                org = Organization(
                    name=dept["org_name"], type=dept["org_type"], dept_code=dept["dept_code"],
                    invite_code=_invite(), mcp_connections=dept["mcp_connections"],
                    dept_prompts=dept.get("dept_prompts"), a2a_url=dept.get("a2a_url"),
                )
                db.add(org)
                await db.flush()
                print(f"[创建] {dept['org_name']} ({dept['dept_code']})")
            else:
                org.mcp_connections = dept["mcp_connections"]
                if "dept_prompts" in dept:
                    org.dept_prompts = dept["dept_prompts"]
                if "a2a_url" in dept:
                    org.a2a_url = dept["a2a_url"]
                print(f"[更新] {dept['org_name']} — mcp_connections + dept_prompts")

            user = await db.scalar(select(User).where(User.email == dept["username"]))
            if user is None:
                user = User(email=dept["username"], hashed_password=_hash(dept["password"]), org_id=org.id)
                db.add(user)
                await db.flush()
                print(f"  [创建] 用户: {dept['username']}")
            else:
                if user.org_id != org.id:
                    user.org_id = org.id
                print(f"  [跳过] 用户: {dept['username']}")

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
                print(f"  [跳过] 知识库: {dept['kb_name']} (kb_id={kb.id})")

            kb_id = kb.id
            await db.commit()
        return kb_id

    async def _ingest(sf, pipeline, kb_id, doc_path):
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
                print(f"  [跳过] {doc_path.name}")
                return
            doc = Document(kb_id=kb_id, filename=doc_path.name, status=DocumentStatus.PROCESSING.value)
            db.add(doc)
            await db.flush()
            doc_id = doc.id
            await db.commit()

        print(f"  [摄入] {doc_path.name} ...")
        result = await asyncio.to_thread(pipeline.run, [doc_path], kb_id, doc_path.name, doc_id)

        async with sf() as db:
            doc = await db.get(Document, doc_id)
            if result.succeeded == 1:
                doc.status = DocumentStatus.READY.value
                print(f"  [完成] {doc_path.name}: {result.total_chunks_written} chunks")
            else:
                doc.status = DocumentStatus.ERROR.value
                doc.error_message = result.errors.get(doc_path.name, "未知错误")
                print(f"  [失败] {doc_path.name}: {doc.error_message}")
            await db.commit()

    async def _clear_kb(sf, store, kb_id):
        print(f"  [清除] Milvus 向量 kb_id={kb_id} ...")
        await asyncio.to_thread(store.delete_by_kb, kb_id)
        async with sf() as db:
            result = await db.execute(delete(Document).where(Document.kb_id == kb_id))
            await db.commit()
        print(f"  [清除] {result.rowcount} 条文档记录")

    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, pool_size=2, connect_args={"ssl": False}
    )
    sf = async_sessionmaker(engine, expire_on_commit=False)
    store = MilvusStore(MilvusStoreConfig(uri=settings.milvus_uri))
    pipeline = IngestionPipeline(
        embedder=OpenAIEmbedder(),
        store=store,
        splitter=ParentChildSplitter(ParentChildConfig(parent_chunk_size=512, child_chunk_size=128, child_overlap=16)),
    )

    if clear:
        scope = f"部门 {only}" if only else "所有知识库"
        print(f"--clear 模式：将清除 {scope} 数据后重新摄入\n")

    for dept in DEPARTMENTS:
        if only and dept["dept_code"] != only:
            continue
        kb_id = await _setup(sf, dept)
        if clear:
            await _clear_kb(sf, store, kb_id)
        doc_files = _docs(dept["doc_dir"])
        if not doc_files:
            print(f"  [警告] 未找到文档: {dept['doc_dir']}")
        else:
            for f in doc_files:
                await _ingest(sf, pipeline, kb_id, f)
        print()

    print("=== 部门种子数据写入完成 ===")
    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="部门种子脚本（需在 backend/ 目录下执行）")
    parser.add_argument("--clear", action="store_true", help="清除知识库后重新摄入")
    parser.add_argument("--only", default=None, metavar="DEPT_CODE", help="只处理指定 dept_code")
    args = parser.parse_args()
    asyncio.run(run(clear=args.clear, only=args.only))


if __name__ == "__main__":
    main()
