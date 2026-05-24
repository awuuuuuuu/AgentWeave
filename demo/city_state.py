"""
城市状态数据库初始化脚本

生成 demo/city_state.db（5张表），种子数据与 demo/ 下各文档数字严格对应。

运行：uv run python demo/city_state.py
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).parent / "city_state.db"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")


async def init_db() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
        logger.info("已删除旧数据库")

    async with aiosqlite.connect(DB_PATH) as db:
        # ── 建表 ─────────────────────────────────────────────────────────────
        await db.executescript("""
            CREATE TABLE hospitals (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                icu_available INTEGER NOT NULL,
                emergency_available INTEGER NOT NULL
            );

            CREATE TABLE ambulances (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT '待命',
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                hospital_id TEXT NOT NULL,
                dest_lat REAL,
                dest_lng REAL,
                patient_type TEXT,
                FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
            );

            CREATE TABLE intersections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                mode TEXT NOT NULL DEFAULT '正常',
                mode_expires_at TEXT
            );

            CREATE TABLE warehouse_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit TEXT NOT NULL,
                alert_threshold INTEGER NOT NULL
            );

            CREATE TABLE sensor_readings (
                id TEXT PRIMARY KEY,
                sensor_type TEXT NOT NULL,
                location TEXT NOT NULL,
                lat REAL NOT NULL DEFAULT 0,
                lng REAL NOT NULL DEFAULT 0,
                current_value REAL NOT NULL,
                unit TEXT NOT NULL,
                threshold REAL,
                is_alarm INTEGER NOT NULL DEFAULT 0
            );
        """)

        # ── hospitals ─────────────────────────────────────────────────────────
        # 医院坐标分布在事故点（港城大道×汉北路南 [117.7280, 39.1180]）约 2km 范围，
        # 既位于 ERPG-1 (~488m) 之外，又与扩散圈/警戒圈同框可见
        await db.executemany(
            "INSERT INTO hospitals VALUES (?,?,?,?,?,?)",
            [
                ("H1", "滨海新区北塘医院",   39.1080, 117.7400,  5, 12),  # ~2.0km SE
                ("H2", "汉沽人民医院",       39.1280, 117.7400,  3,  8),  # ~2.0km NE
                ("H3", "滨海新区中心医院",   39.1180, 117.7050,  2,  6),  # ~2.0km W
                ("H4", "天津港口医院",       39.1000, 117.7280,  1,  4),  # ~2.0km S
            ],
        )

        # ── ambulances ────────────────────────────────────────────────────────
        # 救护车停放于对应医院，均在事故点约 2km 范围
        await db.executemany(
            "INSERT INTO ambulances (id, status, lat, lng, hospital_id) VALUES (?,?,?,?,?)",
            [
                ("A1", "待命", 39.1082, 117.7402, "H1"),  # H1 停车区
                ("A2", "待命", 39.1078, 117.7398, "H1"),  # H1 停车区
                ("A3", "待命", 39.1282, 117.7402, "H2"),  # H2 停车区
                ("A4", "出车", 39.1230, 117.7340, "H2"),  # 正在出车途中
                ("A5", "待命", 39.1182, 117.7054, "H3"),  # H3 停车区
            ],
        )

        # ── intersections ─────────────────────────────────────────────────────
        # 路口坐标调整至港城大道附近（事故点 0.5-3km 范围）
        await db.executemany(
            "INSERT INTO intersections (id, name, lat, lng, mode) VALUES (?,?,?,?,?)",
            [
                ("S1", "港城大道×渤海路",   39.1235, 117.7120, "正常"),  # 700m SW
                ("S2", "港城大道×兴业路",   39.1210, 117.7100, "正常"),  # 1.0km SW
                ("S3", "港城大道×新港路",   39.1195, 117.7082, "正常"),  # 1.2km SW
                ("S4", "港城大道×汉北路",   39.1267, 117.7200, "正常"),  # 500m SE
                ("S5", "汉北路×滨海北路",   39.1340, 117.7082, "正常"),  # 800m NW
                ("S6", "港城大道×海港路",   39.1380, 117.7178, "正常"),  # 1.0km NE
                ("S7", "蓟运河路×滨海路",   39.1130, 117.7380, "正常"),  # 2.3km SE
                ("S8", "北塘大街×港城路",   39.1310, 117.7212, "正常"),  # 600m NE
            ],
        )

        # ── warehouse_inventory ───────────────────────────────────────────────
        inventory = [
            ("防毒面具A级",   "个人防护",  50, "个",  10),
            ("重型防化服",    "个人防护",  20, "套",   5),
            ("轻型防化服",    "个人防护",  40, "套",   8),
            ("空气呼吸器",    "个人防护",  30, "套",   6),
            ("氨气检测仪",    "检测设备",  15, "台",   3),
            ("急救箱",        "医疗物资",  60, "个",  12),
            ("医用氧气瓶",    "医疗物资",  80, "瓶",  20),
            ("洗眼液",        "医疗物资", 200, "瓶",  50),
            ("急救担架",      "医疗物资",  25, "副",   5),
            ("隔离警戒带",    "现场处置", 100, "卷",  20),
            ("消防水带",      "消防器材",  40, "卷",   8),
            ("泡沫灭火剂",    "消防器材",  30, "桶",   6),
            ("事故照明灯",    "现场处置",  20, "台",   4),
            ("通信对讲机",    "通信设备",  30, "台",   6),
            ("除污洗消粉",    "洗消物资",  50, "袋",  10),
            ("中和剂（稀盐酸）", "洗消物资", 100, "升", 20),
            ("急救药品包",    "医疗物资",  40, "套",   8),
            ("注射用解毒药",  "医疗物资", 200, "支",  40),
            ("输液套装",      "医疗物资", 150, "套",  30),
            ("吸氧面罩",      "医疗物资", 100, "个",  20),
            ("病人转运车",    "转运设备",   5, "辆",   2),
            ("救援帐篷",      "现场处置",  10, "顶",   2),
            ("应急食品",      "后勤物资", 300, "份",  60),
            ("饮用水",        "后勤物资", 500, "升", 100),
            ("临时围栏",      "现场处置", 200, "节",  40),
            ("发电机",        "动力设备",   8, "台",   2),
            ("便携扩音器",    "通信设备",  15, "台",   3),
            ("消毒剂",        "洗消物资",  80, "升",  16),
            ("急救毯",        "医疗物资", 100, "张",  20),
            ("应急照明弹",    "现场处置",  50, "发",  10),
        ]
        await db.executemany(
            "INSERT INTO warehouse_inventory (name, category, quantity, unit, alert_threshold) VALUES (?,?,?,?,?)",
            inventory,
        )

        # ── sensor_readings ───────────────────────────────────────────────────
        # 事故中心：[117.7280, 39.1180]（港城大道×汉北路南，工业区）
        # 列顺序：id, sensor_type, location, lat, lng, current_value, unit, threshold, is_alarm
        sensors = [
            # 氨气浓度 N1-N8（ppm，告警阈值 25ppm）
            ("N1",  "氨气浓度", "事故点正北50m",  39.1185, 117.728,  890.0, "ppm",  25.0, 1),
            ("N2",  "氨气浓度", "事故点正东50m",  39.118, 117.7286,  620.0, "ppm",  25.0, 1),
            ("N3",  "氨气浓度", "事故点正南50m",  39.1175, 117.728,  340.0, "ppm",  25.0, 1),
            ("N4",  "氨气浓度", "事故点正西50m",  39.118, 117.7274,  185.0, "ppm",  25.0, 1),
            ("N5",  "氨气浓度", "外围北200m",     39.1198, 117.728,  120.0, "ppm",  25.0, 1),
            ("N6",  "氨气浓度", "外围东200m",     39.118, 117.7303,   78.0, "ppm",  25.0, 1),
            ("N7",  "氨气浓度", "外围南200m",     39.1162, 117.728,   45.0, "ppm",  25.0, 1),
            ("N8",  "氨气浓度", "外围西200m",     39.118, 117.7257,   32.0, "ppm",  25.0, 1),
            # 温度传感器 T1-T5（℃）
            ("T1",  "温度", "储罐区A",    39.1182, 117.7277,  42.0, "℃",   None, 0),
            ("T2",  "温度", "储罐区B",    39.1178, 117.7283,  38.5, "℃",   None, 0),
            ("T3",  "温度", "管道控制室", 39.1181, 117.7285,  31.2, "℃",   None, 0),
            ("T4",  "温度", "厂区入口",   39.1186, 117.7287,  27.8, "℃",   None, 0),
            ("T5",  "温度", "操作间",     39.1177, 117.7276,  25.3, "℃",   None, 0),
            # 压力传感器 P1-P4（MPa，告警阈值 1.5MPa）
            ("P1",  "压力", "主管道",     39.118, 117.7279,  2.8, "MPa",  1.5, 1),
            ("P2",  "压力", "储罐A",      39.1181, 117.7277,  2.1, "MPa",  1.5, 1),
            ("P3",  "压力", "储罐B",      39.1179, 117.7282,  0.8, "MPa",  1.5, 0),
            ("P4",  "压力", "安全阀出口", 39.118, 117.7285,  0.0, "MPa",  1.5, 0),
            # 风速/风向
            ("W1",  "风速", "气象站A",    39.1184, 117.7285,  3.2, "m/s", None, 0),
            ("W2",  "风速", "气象站B",    39.1176, 117.7275,  4.5, "m/s", None, 0),
            ("W3",  "风速", "厂区顶部",   39.1183, 117.728,  2.1, "m/s", None, 0),
            ("D1",  "风向", "气象站A",    39.1184, 117.7285, 202.0, "°",  None, 0),
            # 一氧化碳 CO1-CO3（ppm，告警阈值 50ppm）
            ("CO1", "一氧化碳", "储罐区",    39.1182, 117.7278,  82.0, "ppm", 50.0, 1),
            ("CO2", "一氧化碳", "生产车间",  39.1178, 117.7279,  34.0, "ppm", 50.0, 0),
            ("CO3", "一氧化碳", "厂区外围",  39.1186, 117.729,   8.5, "ppm", 50.0, 0),
            # 可燃气体 F1-F3（%LEL）
            ("F1",  "可燃气体", "储罐区A",   39.1182, 117.7277, 15.2, "%LEL", 10.0, 1),
            ("F2",  "可燃气体", "管道区",    39.1179, 117.7281,  4.8, "%LEL", 10.0, 0),
            ("F3",  "可燃气体", "厂区入口",  39.1186, 117.7287,  0.3, "%LEL", 10.0, 0),
            # 噪声 V1（dB）
            ("V1",  "噪声", "储罐区",     39.1182, 117.7278, 98.5, "dB",  None, 0),
            # 烟雾 SM1-SM3（%obs，ID 前缀改为 SM 避免与 intersections S1-S3 冲突）
            ("SM1", "烟雾", "储罐区A",    39.1182, 117.7277, 62.0, "%obs", 30.0, 1),
            ("SM2", "烟雾", "生产车间",   39.1178, 117.7279, 18.0, "%obs", 30.0, 0),
            ("SM3", "烟雾", "厂区外围",   39.1186, 117.729,  2.5, "%obs", 30.0, 0),
        ]
        await db.executemany(
            "INSERT INTO sensor_readings VALUES (?,?,?,?,?,?,?,?,?)",
            sensors,
        )

        await db.commit()

    logger.info("数据库初始化完成：%s", DB_PATH)
    logger.info("表：hospitals=%d, ambulances=%d, intersections=%d, warehouse=%d, sensors=%d",
                4, 5, 8, len(inventory), len(sensors))


if __name__ == "__main__":
    asyncio.run(init_db())
