"""Demo 工具：按坐标设置传感器告警区域，或重置所有传感器为正常。

运行：
    uv run python demo/scripts/alarm_zone.py --lat 31.238 --lng 121.497 --radius 3
    uv run python demo/scripts/alarm_zone.py --reset
"""
from __future__ import annotations

import argparse
import asyncio
import math
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "city_state.db"


def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


async def run(lat=None, lng=None, radius=3.0, reset=False) -> None:
    import aiosqlite

    if not _DB_PATH.exists():
        print(f"错误：{_DB_PATH} 不存在，请先运行 seed.py init")
        return

    async with aiosqlite.connect(_DB_PATH) as db:
        if reset:
            async with db.execute(
                "SELECT id, sensor_type, location, current_value, threshold FROM sensor_readings"
            ) as cur:
                sensors = await cur.fetchall()
            rows = []
            for sid, stype, loc, cur_val, thr in sensors:
                new_val = round(thr * 0.3, 2) if thr is not None else cur_val
                await db.execute("UPDATE sensor_readings SET is_alarm=0, current_value=? WHERE id=?", (new_val, sid))
                rows.append((sid, stype, loc, cur_val, new_val, thr))
            await db.commit()
            print(f"\n已重置全部 {len(rows)} 个传感器为正常状态：\n")
            print(f"{'ID':<10} {'类型':<10} {'位置':<22} {'原值':<10} {'恢复值':<10} {'阈值'}")
            print("-" * 70)
            for sid, stype, loc, old, new, thr in rows:
                print(f"{sid:<10} {stype:<10} {loc:<22} {old:<10} {new:<10} {thr if thr else '—'}")
        else:
            async with db.execute(
                "SELECT id, sensor_type, location, lat, lng, current_value, threshold, is_alarm FROM sensor_readings"
            ) as cur:
                sensors = await cur.fetchall()
            affected = []
            for sid, stype, loc, slat, slng, cur_val, thr, is_alarm in sensors:
                dist = _haversine_km(lat, lng, slat, slng)
                if dist > radius:
                    continue
                new_val = round(thr * 1.5, 2) if thr is not None else round(cur_val * 2, 2)
                await db.execute("UPDATE sensor_readings SET is_alarm=1, current_value=? WHERE id=?", (new_val, sid))
                affected.append((sid, stype, loc, round(dist, 2), cur_val, new_val, thr, bool(is_alarm)))
            await db.commit()

            if not affected:
                print(f"半径 {radius}km 范围内没有找到传感器。")
                return
            print(f"\n中心点 ({lat}, {lng})，半径 {radius}km 内影响 {len(affected)} 个传感器：\n")
            print(f"{'ID':<10} {'类型':<10} {'位置':<22} {'距离km':<8} {'原值':<10} {'告警值':<10} {'阈值'}")
            print("-" * 82)
            for sid, stype, loc, dist, old, new, thr, was in sorted(affected, key=lambda x: x[3]):
                flag = " [已告警]" if was else ""
                print(f"{sid:<10} {stype:<10} {loc:<22} {dist:<8} {old:<10} {new:<10} {thr if thr else '—'}{flag}")
            print(f"\n已将以上 {len(affected)} 个传感器设为告警状态（is_alarm=1）。")


def main():
    parser = argparse.ArgumentParser(description="设置或重置传感器告警区域")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lng", type=float)
    parser.add_argument("--radius", type=float, default=3.0, help="搜索半径（公里，默认 3.0）")
    parser.add_argument("--reset", action="store_true", help="重置所有传感器为正常状态")
    args = parser.parse_args()

    if not args.reset and (args.lat is None or args.lng is None):
        parser.error("请指定 --lat 和 --lng，或使用 --reset")

    asyncio.run(run(lat=args.lat, lng=args.lng, radius=args.radius, reset=args.reset))


if __name__ == "__main__":
    main()
