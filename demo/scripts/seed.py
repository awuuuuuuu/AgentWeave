"""统一入口 — 按子命令分派到 scripts/ 下各独立脚本。

各脚本也可单独运行：
    uv run python demo/scripts/init_db.py
    uv run python demo/scripts/geo_data.py
    uv run python demo/scripts/alarm_zone.py --lat 31.238 --lng 121.497

统一用法：
    uv run python demo/scripts/seed.py init
    uv run python demo/scripts/seed.py geo
    uv run python demo/scripts/seed.py depts [--clear] [--only DEPT_CODE]
    uv run python demo/scripts/seed.py alarm --lat 31.238 --lng 121.497 [--radius 3]
    uv run python demo/scripts/seed.py alarm --reset
    uv run python demo/scripts/seed.py all [--clear]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import init_db
import geo_data
import alarm_zone
import departments


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AgentWeave Demo 统一入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init",  help="初始化 city_state.db")
    sub.add_parser("geo",   help="从高德 API 导入上海医院 / 消防站数据（幂等）")

    dp = sub.add_parser("depts", help="初始化部门机构 / 用户 / 知识库并摄入文档（需在 backend/ 下执行）")
    dp.add_argument("--clear", action="store_true", help="清除知识库后重新摄入")
    dp.add_argument("--only", default=None, metavar="DEPT_CODE", help="只处理指定 dept_code")

    ap = sub.add_parser("alarm", help="设置或重置传感器告警区域")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lng", type=float)
    ap.add_argument("--radius", type=float, default=3.0, help="搜索半径（公里，默认 3.0）")
    ap.add_argument("--reset", action="store_true", help="重置所有传感器为正常状态")

    allp = sub.add_parser("all", help="按顺序执行：init → geo → depts")
    allp.add_argument("--clear", action="store_true", help="depts 阶段清除知识库后重新摄入")

    args = parser.parse_args()

    if args.cmd == "init":
        asyncio.run(init_db.run())

    elif args.cmd == "geo":
        asyncio.run(geo_data.run())

    elif args.cmd == "depts":
        asyncio.run(departments.run(clear=args.clear, only=args.only))

    elif args.cmd == "alarm":
        if not args.reset and (args.lat is None or args.lng is None):
            ap.error("请指定 --lat 和 --lng，或使用 --reset")
        asyncio.run(alarm_zone.run(lat=args.lat, lng=args.lng, radius=args.radius, reset=args.reset))

    elif args.cmd == "all":
        async def _all():
            await init_db.run()
            try:
                await geo_data.run()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "geo_data 阶段跳过（%s），继续初始化部门数据", e
                )
            await departments.run(clear=args.clear, only=None)
        asyncio.run(_all())


if __name__ == "__main__":
    main()
