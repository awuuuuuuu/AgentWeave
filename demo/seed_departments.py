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
    # ── 顶层协调员（Crew Orchestrator）────────────────────────────────────────
    {
        "dept_code": "cmd_center",
        "org_name": "应急指挥中心",
        "org_type": "command",        # 区别于子部门 "department"，顶层编排角色
        "username": "coordinator@pku.com",
        "password": "demo1234",
        "kb_name": "应急指挥中心知识库",
        "doc_dir": DEMO_DIR / "cmd-center",
        "mcp_connections": [],        # orchestrator 不直接调工具，由子部门各自持有 MCP
        "dept_prompts": {
            "supervisor_hints": (
                "你是应急指挥中心总协调员（Crew Orchestrator），负责接收事故报告并协调下属五个部门同步响应。\n"
                "【初始研判】先路由 researcher 检索匹配的应急预案等级与启动条件；同步路由 analyst 通过传感器确认现场态势（浓度/风向/告警级别）。\n"
                "【分派原则】根据研判结果，按如下专责将子任务分派给相应部门（在上层 Crew 中体现为路由决策）：\n"
                "  · EN 环保局    ──── 大气扩散建模 + 疏散方向\n"
                "  · ME 医疗急救  ──── 伤员接诊 + 救护车调度（含 HITL）\n"
                "  · TR 交通管控  ──── 路口信号管制 + 疏散通道（含 HITL）\n"
                "  · LG 应急物资  ──── 防护物资调拨（含 HITL）\n"
                "  · SF 企业安全  ──── 根因分析 + 现场处置规程\n"
                "【HITL 原则】涉及写操作（派车/路口信号/物资调拨）的部门子任务，各部门 analyst 完成评估后必须经过 HITL 审批，不得自行执行。\n"
                "【汇总简报】所有部门响应完成（或首批结果就绪）后，路由 reporter 生成总指挥简报，"
                "格式：事故概况 → 各部门响应状态 → 待审批项 → 下一步行动建议。"
            ),
            "analyst_context": (
                "你负责初始态势研判。依次调用：\n"
                "1. get_incident_timeline() — 梳理事故时间线与已知伤亡信息\n"
                "2. get_critical_alarms()   — 确认超阈值告警传感器及危险区范围\n"
                "3. get_sensor_readings(sensor_type='氨气浓度') — 获取核心危险物浓度读数\n"
                "整合后输出：事故类型 / 当前预案等级 / 关键数据 / 建议启动的部门，供总协调员决策分派。"
            ),
        },
    },
    # ── 五个专责部门 ──────────────────────────────────────────────────────────
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
                "description": "大气扩散模型（只读）：estimate_release_rate 根据传感器浓度/距离/风速反推泄漏速率（g/s）；calculate_plume 输入泄漏速率和气象参数计算 ERPG-1/2/3 疏散半径；get_evacuation_direction 根据风向给出疏散方向和优先管控路口",
            },
            {
                "name": "enterprise_sensor",
                "url": "http://localhost:8105/mcp",
                "description": "企业传感器（只读）：get_sensor_readings 查询实时读数（sensor_type 可选 氨气浓度/风速/风向/压力/温度）；get_critical_alarms 获取超阈值报警传感器；get_incident_timeline 返回事故时间线",
            },
        ],
        "dept_prompts": {
            "supervisor_hints": (
                "扩散/疏散类问题：先路由 researcher 检索毒性参数和分级标准，再路由 analyst 执行扩散建模（依次调用传感器读取、泄漏速率推算、扩散半径计算）。\n"
                "analyst 完成后路由 reporter 输出疏散方案结论。"
            ),
            "analyst_context": (
                "【完整调用链，必须严格按顺序执行，不得跳过任何步骤】\n"
                "步骤1：调用 get_sensor_readings(sensor_type='氨气浓度')\n"
                "  → 取返回列表中 current_value 最大的一条读数\n"
                "  → 从该条记录的 location 字段解析传感器距泄漏点的距离（如'正北50m' → distance_m=50）\n"
                "  → 若工具调用失败或返回空列表，在报告中注明'氨气浓度数据不可用'，终止后续步骤并输出当前已知信息\n\n"
                "步骤2：调用 get_sensor_readings(sensor_type='风速')\n"
                "  → 取返回列表中气象站类传感器的 current_value 作为 wind_speed_ms\n"
                "  → 若返回多条，取最近更新（update_time 最新）的一条\n"
                "  → 若工具调用失败，使用保守默认值 wind_speed_ms=1.0，并在报告中标注为估算值\n\n"
                "步骤3：调用 get_sensor_readings(sensor_type='风向')\n"
                "  → 取返回列表中风向传感器的 current_value 作为 wind_dir_deg\n"
                "  → 若工具调用失败，使用默认值 wind_dir_deg=0，并在报告中标注为估算值\n\n"
                "步骤4：调用 estimate_release_rate(\n"
                "    concentration_ppm=<步骤1最大浓度>,\n"
                "    distance_m=<步骤1解析的距离>,\n"
                "    wind_speed_ms=<步骤2风速>\n"
                "  ) → 得到 release_rate_gs\n"
                "  → 若工具调用失败，使用保守默认值 release_rate_gs=10.0，并在报告中标注为估算值\n\n"
                "步骤5：调用 calculate_plume(\n"
                "    lat=<从 get_incident_timeline 或事故描述中提取的事故点纬度，默认 39.0251>,\n"
                "    lng=<从 get_incident_timeline 或事故描述中提取的事故点经度，默认 117.7451>,\n"
                "    wind_speed_ms=<步骤2风速>,\n"
                "    wind_dir_deg=<步骤3风向>,\n"
                "    release_rate_gs=<步骤4结果>\n"
                "  ) → 得到 ERPG-1/2/3 疏散半径\n"
                "  → 若工具调用失败，在报告中注明'扩散半径计算不可用'，仅输出已收集的传感器数据\n\n"
                "【禁止使用猜测值或跳过步骤4直接调用 calculate_plume；默认值仅在工具明确失败时使用】\n\n"
                "【MCP引用标注】在汇报中引用MCP工具获取的实时数据时，请在数据后加[M数字]标注，"
                "数字与工具调用顺序对应：第1次工具调用的结果加[M1]，第2次加[M2]，依此类推。"
                "例如：「氨气浓度890ppm[M1]，风速3.2m/s[M2]，风向202°[M3]，"
                "泄漏速率≈47g/s[M4]，ERPG-2半径890m[M5]」"
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
                "含'【研判阶段】'：先路由 researcher 检索接诊规程，再路由 analyst 调用 get_hospital_capacity 和 list_ambulances，路由 reporter 输出建议。\n"
                "含'【执行阶段】'：直接路由 analyst，current_task 开头必须保留「【执行阶段】已授权」标记，analyst 完成后路由 reporter。\n"
                "含'【分析阶段】'：视内容路由 researcher 或 analyst，无需 HITL。\n"
                "其他任务（路线规划）：analyst 完成后直接 reporter，无需 HITL。"
            ),
            "analyst_context": (
                "【执行阶段】当任务含「【执行阶段】」时，必须调用 dispatch_ambulance 实际执行调度（不得仅输出评估建议）：\n"
                "先调 get_hospital_capacity() + list_ambulances() 确认资源，再用 geocode() 获取事故地点坐标，\n"
                "最后调用 dispatch_ambulance(ambulance_id, dest_lat, dest_lng, patient_type) 完成派车（已授权，无需 HITL）。\n"
                "【研判阶段】调用 get_hospital_capacity() 和 list_ambulances() 评估后整理方案即可，"
                "勿调用 dispatch_ambulance（需 HITL）。严禁引用知识库历史数字作为实时床位。\n"
                "纯路线规划任务直接调用 geocode/plan_driving_route 并输出结果。\n"
                "【geocode 地址规范】本部门位于天津市滨海新区，调用 geocode 时必须补全城市前缀，"
                "例如：'泰达医院' → '天津市滨海新区泰达医院'，'港城大道388号' → '天津市滨海新区港城大道388号'。\n"
                "【MCP引用标注】在汇报中引用MCP工具获取的实时数据时，请在数据后加[M数字]标注，"
                "数字与工具调用顺序对应。例如：「泰达医院ICU可用5张[M1]，A01救护车待命[M2]」"
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
                "含'【研判阶段】'：先路由 researcher（current_task 须明确检索目标，如「Ⅲ级预案路口信号管制模式与路口清单」，禁止使用'相关内容'等模糊表述），"
                "再路由 analyst 调用 list_intersections 查询路口实时状态。"
                "若任务含信号切换写操作请求，analyst 完成评估后路由 hitl 审批；否则路由 reporter 输出方案。\n"
                "含'【执行阶段】'：直接路由 analyst（已获上层指挥中心 HITL 授权，本级无需再路由 hitl），analyst 完成后路由 reporter。\n"
                "含'【分析阶段】'：视内容路由 researcher 或 analyst，无需 HITL。\n"
                "其他任务（路线规划）：analyst 调用 geocode + plan_driving_route 完成后直接 reporter，无需 HITL。"
            ),
            "analyst_context": (
                "【路口管控任务】先调 list_intersections() 获取所有路口当前信号模式，整理查询结果。\n"
                "【执行阶段任务】当任务中含「【执行阶段】」时，先调 list_intersections() 查看路口当前状态，"
                "然后调用信号管控工具执行切换（已获上层授权）。\n"
                "【研判阶段任务】apply_evacuation_plan 和 set_mode 是写操作，研判阶段只能读取状态并输出文字方案，写操作须经 HITL 审批。\n"
                "【路线+信号联合任务】先用 geocode() 获取起点和终点坐标，调用 plan_driving_route() 规划路线，"
                "然后调用 list_intersections() 获取沿线路口现状，整理后停止。\n"
                "【仅路线规划任务】用 geocode() 获取坐标，调用 plan_driving_route() 完成路线规划，直接 __end__。\n"
                "【geocode 地址规范】本部门位于天津市滨海新区，调用 geocode 时必须补全城市前缀，"
                "例如：'港城大道388号' → '天津市滨海新区港城大道388号'。\n"
                "【MCP引用标注】在汇报中引用MCP工具获取的实时数据时，请在数据后加[M数字]标注，"
                "例如：「S3路口当前正常模式[M1]，疏散路线全长2.3km[M2]」"
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
                "含'【研判阶段】'：先路由 researcher（current_task 须明确写出要检索的具体信息，如「Ⅲ级标准包物资清单及数量要求」，禁止使用'相关内容'等模糊表述），"
                "再路由 analyst 调用 check_alerts 和 get_inventory 查询。"
                "若任务含调拨出库请求，analyst 完成评估后路由 hitl 审批；否则路由 reporter 输出评估。\n"
                "含'【执行阶段】'：直接路由 analyst，current_task 开头必须保留「【执行阶段】已授权」标记，analyst 完成后路由 reporter。\n"
                "含'【分析阶段】'：视内容路由 researcher 或 analyst，无需 HITL。\n"
                "其他查询（不含阶段标记，如库存告警、补充建议等）：先路由 researcher（current_task 须明确写出要检索的具体数值，"
                "如「各物资最低储备量（预警线）数值及不足时补充措施」，禁止使用'相关内容'等泛化表述），"
                "再路由 analyst 调用 check_alerts 对照分析，reporter 整合输出；无需 HITL。"
            ),
            "analyst_context": (
                "【执行阶段】当任务含「【执行阶段】」时：调用 check_alerts() 和 get_inventory() 核实库存后，"
                "立即调用 allocate_standard_pack（按预案等级）或 allocate_custom（自定义）执行调拨（上层已授权，无需 HITL）。\n"
                "【研判阶段】调用 check_alerts() 和 get_inventory() 后整理评估方案即可，"
                "勿调用 allocate_*（写操作，需 HITL）。\n"
                "【MCP引用标注】在汇报中引用MCP工具获取的实时数据时，请在数据后加[M数字]标注，"
                "数字与工具调用顺序对应。例如：「防化服库存12套[M1]，空气呼吸器8套告警[M2]」"
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
                "含'【研判阶段】'：严格按三步执行——"
                "①路由 researcher 检索事故处置规程（1次，检索完即停）；"
                "②无论 researcher 是否找到文档，必须继续路由 analyst 调用 get_critical_alarms 和 get_incident_timeline 获取实时传感器数据；"
                "③路由 reporter 综合知识库结果和实时数据生成报告。analyst 步骤不可跳过。\n"
                "含'【执行阶段】'或'【分析阶段】'：所有工具只读，路由 analyst 进行实时数据分析，reporter 输出，无需 HITL。\n"
                "其他任务：视内容路由 researcher 或 analyst，所有传感器工具只读，无需 HITL。"
            ),
            "analyst_context": (
                "所有工具只读，可直接调用无需 HITL。根因分析时同时调 get_incident_timeline() 和 get_critical_alarms()；"
                "分析压力异常时同时查 sensor_type='压力' 和 sensor_type='温度' 做关联分析。\n"
                "【MCP引用标注】在汇报中引用MCP工具获取的实时数据时，请在数据后加[M数字]标注，"
                "数字与工具调用顺序对应。例如：「储罐压力1.8MPa超限[M1]，泄漏起始时间14:23[M2]」"
            ),
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
    dept_filter: str | None = None,
) -> None:
    for dept in DEPARTMENTS:
        if dept_filter and dept["dept_code"] != dept_filter:
            continue
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
    parser.add_argument(
        "--dept",
        default=None,
        help="只处理指定部门（dept_code），如 emergency_supplies，留空则处理全部",
    )
    args = parser.parse_args()

    if args.clear:
        scope = f"部门 {args.dept}" if args.dept else "所有知识库"
        print(f"⚠️  --clear 模式：将清除 {scope} 数据后重新摄入\n")

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

    await seed(sf, pipeline, store, clear=args.clear, dept_filter=args.dept)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
