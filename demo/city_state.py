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
                current_value REAL NOT NULL,
                unit TEXT NOT NULL,
                threshold REAL,
                is_alarm INTEGER NOT NULL DEFAULT 0
            );
        """)

        # ── hospitals ─────────────────────────────────────────────────────────
        await db.executemany(
            "INSERT INTO hospitals VALUES (?,?,?,?,?,?)",
            [
                ("H1", "泰达医院",         39.0089, 117.7356, 5, 12),
                ("H2", "滨海新区第二医院", 39.0231, 117.7192, 3,  8),
                ("H3", "滨海医院",         39.0412, 117.7089, 2,  6),
                ("H4", "中医联合医院",     38.9934, 117.7521, 1,  4),
            ],
        )

        # ── ambulances ────────────────────────────────────────────────────────
        await db.executemany(
            "INSERT INTO ambulances (id, status, lat, lng, hospital_id) VALUES (?,?,?,?,?)",
            [
                ("A1", "待命", 39.0091, 117.7358, "H1"),
                ("A2", "待命", 39.0088, 117.7354, "H1"),
                ("A3", "待命", 39.0232, 117.7194, "H2"),
                ("A4", "出车", 39.0315, 117.7250, "H2"),
                ("A5", "待命", 39.0413, 117.7091, "H3"),
            ],
        )

        # ── intersections ─────────────────────────────────────────────────────
        await db.executemany(
            "INSERT INTO intersections (id, name, lat, lng, mode) VALUES (?,?,?,?,?)",
            [
                ("S1", "港城大道×塘汉路",   39.0235, 117.7423, "正常"),
                ("S2", "港城大道×第五大街", 39.0214, 117.7401, "正常"),
                ("S3", "港城大道×中央大道", 39.0198, 117.7378, "正常"),
                ("S4", "塘汉路×泰达大街",   39.0267, 117.7445, "正常"),
                ("S5", "泰达大街×洞庭路",   39.0189, 117.7312, "正常"),
                ("S6", "中央大道×洞庭路",   39.0172, 117.7289, "正常"),
                ("S7", "洞庭路×第六大街",   39.0156, 117.7334, "正常"),
                ("S8", "第五大街×泰达大街", 39.0201, 117.7367, "正常"),
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
        # 港城大道388号周边传感器（事故现场）
        sensors = [
            # 氨气浓度 N1-N8（ppm，告警阈值 25ppm）
            ("N1", "氨气浓度", "事故点正北50m",  890.0, "ppm", 25.0, 1),
            ("N2", "氨气浓度", "事故点正东50m",  620.0, "ppm", 25.0, 1),
            ("N3", "氨气浓度", "事故点正南50m",  340.0, "ppm", 25.0, 1),
            ("N4", "氨气浓度", "事故点正西50m",  185.0, "ppm", 25.0, 1),
            ("N5", "氨气浓度", "外围北200m",     120.0, "ppm", 25.0, 1),
            ("N6", "氨气浓度", "外围东200m",      78.0, "ppm", 25.0, 1),
            ("N7", "氨气浓度", "外围南200m",      45.0, "ppm", 25.0, 1),
            ("N8", "氨气浓度", "外围西200m",      32.0, "ppm", 25.0, 1),
            # 温度传感器 T1-T5（℃）
            ("T1", "温度", "储罐区A",  42.0, "℃", None, 0),
            ("T2", "温度", "储罐区B",  38.5, "℃", None, 0),
            ("T3", "温度", "管道控制室", 31.2, "℃", None, 0),
            ("T4", "温度", "厂区入口",  27.8, "℃", None, 0),
            ("T5", "温度", "操作间",    25.3, "℃", None, 0),
            # 压力传感器 P1-P4（MPa，告警阈值 1.5MPa）
            ("P1", "压力", "主管道",    2.8, "MPa", 1.5, 1),
            ("P2", "压力", "储罐A",     2.1, "MPa", 1.5, 1),
            ("P3", "压力", "储罐B",     0.8, "MPa", 1.5, 0),
            ("P4", "压力", "安全阀出口", 0.0, "MPa", 1.5, 0),
            # 风速传感器 W1-W3（m/s）
            ("W1", "风速", "气象站A",   3.2, "m/s", None, 0),
            ("W2", "风速", "气象站B",   4.5, "m/s", None, 0),
            ("W3", "风速", "厂区顶部",  2.1, "m/s", None, 0),
            # 风向传感器 D1（°，202° = 南偏西）
            ("D1", "风向", "气象站A",  202.0, "°",  None, 0),
            # 一氧化碳 CO1-CO3（ppm，告警阈值 50ppm）
            ("CO1", "一氧化碳", "储罐区",   82.0, "ppm", 50.0, 1),
            ("CO2", "一氧化碳", "生产车间", 34.0, "ppm", 50.0, 0),
            ("CO3", "一氧化碳", "厂区外围",  8.5, "ppm", 50.0, 0),
            # 可燃气体 F1-F3（%LEL）
            ("F1", "可燃气体", "储罐区A",  15.2, "%LEL", 10.0, 1),
            ("F2", "可燃气体", "管道区",    4.8, "%LEL", 10.0, 0),
            ("F3", "可燃气体", "厂区入口",  0.3, "%LEL", 10.0, 0),
            # 噪声 V1（dB）
            ("V1", "噪声", "储罐区",    98.5, "dB",  None, 0),
            # 烟雾 S1-S3（%obs）
            ("S1", "烟雾", "储罐区A",  62.0, "%obs", 30.0, 1),
            ("S2", "烟雾", "生产车间", 18.0, "%obs", 30.0, 0),
            ("S3", "烟雾", "厂区外围",  2.5, "%obs", 30.0, 0),
        ]
        await db.executemany(
            "INSERT INTO sensor_readings VALUES (?,?,?,?,?,?,?)",
            sensors,
        )

        await db.commit()

    logger.info("数据库初始化完成：%s", DB_PATH)
    logger.info("表：hospitals=%d, ambulances=%d, intersections=%d, warehouse=%d, sensors=%d",
                4, 5, 8, len(inventory), len(sensors))


if __name__ == "__main__":
    asyncio.run(init_db())
