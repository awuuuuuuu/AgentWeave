"""从高德 API 批量导入上海全市医院 / 消防站数据（幂等）。

运行前需设置 AMAP_API_KEY 或 AMAP_SERVICE_KEY 环境变量。

运行：
    uv run python demo/scripts/geo_data.py
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
from pathlib import Path

_DEMO_DIR = Path(__file__).parent.parent
_DB_PATH  = _DEMO_DIR / "city_state.db"
_AMAP_BASE = "https://restapi.amap.com/v3"
_DISTRICTS = [
    "浦东新区", "黄浦区", "徐汇区", "长宁区", "静安区",
    "普陀区", "虹口区", "杨浦区", "闵行区", "宝山区",
    "嘉定区", "金山区", "松江区", "青浦区", "奉贤区", "崇明区",
]


async def run() -> None:
    import httpx
    import aiosqlite
    from dotenv import load_dotenv

    for env_path in [
        _DEMO_DIR.parent / "backend" / ".env",
        _DEMO_DIR.parent / "frontend" / ".env.local",
        _DEMO_DIR.parent / ".env",
    ]:
        if env_path.exists():
            load_dotenv(env_path)

    api_key = (os.environ.get("AMAP_API_KEY", "") or os.environ.get("AMAP_SERVICE_KEY", "")).strip()
    if not api_key:
        raise RuntimeError(
            "未找到高德 API Key。\n"
            "请在 backend/.env 或 frontend/.env.local 中配置：\n"
            "  AMAP_API_KEY=your_key  或  AMAP_SERVICE_KEY=your_key"
        )

    if not _DB_PATH.exists():
        raise RuntimeError(f"{_DB_PATH} 不存在，请先运行 seed.py init")

    print(f"数据库：{_DB_PATH}")
    print(f"API Key：{api_key[:6]}{'*' * (len(api_key) - 6)}")

    async def _fetch(client, keywords, district, page=1, max_retries=3):
        for attempt in range(max_retries):
            try:
                r = await client.get(
                    f"{_AMAP_BASE}/place/text",
                    params={"key": api_key, "keywords": keywords, "city": district,
                            "citylimit": "true", "offset": 25, "page": page, "output": "json"},
                    timeout=10.0,
                )
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                wait = 2 ** attempt
                if attempt < max_retries - 1:
                    print(f"  [重试 {attempt+1}/{max_retries}] {district}/{keywords} p{page}: {e}，{wait}s 后重试")
                    await asyncio.sleep(wait)
                    continue
                print(f"  [跳过] {district}/{keywords} page={page}: {e}")
                return []
            # 高德限流码（CUQPS_HAS_EXCEEDED / USER_DAILY_QUERY_OVER_LIMIT）
            if data.get("infocode") in ("10003", "10044") or data.get("status") == "0":
                wait = 2 ** attempt
                if attempt < max_retries - 1:
                    print(f"  [限流/错误] {data.get('info')} ({data.get('infocode')})，{wait}s 后重试")
                    await asyncio.sleep(wait)
                    continue
                print(f"  [API错误] {data.get('info')}，跳过 {district}/{keywords}")
                return []
            return data.get("pois", [])
        return []

    def _parse_loc(s):
        try:
            lng, lat = map(float, s.strip().split(","))
            return (lat, lng) if (30.6 <= lat <= 31.9 and 120.8 <= lng <= 122.2) else None
        except Exception:
            return None

    def _hospital_mock(name):
        if any(k in name for k in ("附属", "医学院", "大学")):
            return random.randint(10, 20), random.randint(30, 50)
        if any(k in name for k in ("社区", "卫生")):
            return 0, random.randint(5, 10)
        return random.randint(3, 8), random.randint(15, 25)

    async def _max_id(db, table, prefix):
        async with db.execute(f"SELECT id FROM {table}") as cur:
            rows = await cur.fetchall()
        return max((int(r[0][len(prefix):]) for r in rows if r[0].startswith(prefix)
                    and r[0][len(prefix):].isdigit()), default=0)

    async with aiosqlite.connect(_DB_PATH) as db, httpx.AsyncClient() as client:
        # ── 医院 ──────────────────────────────────────────────────────────────
        print("\n=== 导入医院数据 ===")
        h_n = await _max_id(db, "hospitals", "H")
        a_n = await _max_id(db, "ambulances", "A")
        async with db.execute("SELECT name, lat, lng FROM hospitals") as cur:
            seen_h = {f"{r[0]}|{r[1]:.3f}|{r[2]:.3f}" for r in await cur.fetchall()}
        h_total = 0

        for dist in _DISTRICTS:
            added = 0
            print(f"\n  [{dist}] 搜索医院...")
            for page in [1, 2]:
                pois = await _fetch(client, "医院", dist, page)
                await asyncio.sleep(0.3)
                if not pois:
                    break
                h_before = h_n
                for poi in pois:
                    name = poi.get("name", "").strip()
                    coords = _parse_loc(poi.get("location", ""))
                    if not coords:
                        continue
                    lat, lng = coords
                    key = f"{name}|{lat:.3f}|{lng:.3f}"
                    if key in seen_h:
                        continue
                    seen_h.add(key)
                    h_n += 1
                    icu, emer = _hospital_mock(name)
                    try:
                        await db.execute(
                            "INSERT OR IGNORE INTO hospitals (id,name,lat,lng,icu_available,emergency_available) VALUES (?,?,?,?,?,?)",
                            (f"H{h_n}", name, lat, lng, icu, emer),
                        )
                        added += 1
                    except Exception as e:
                        print(f"  [失败] {name}: {e}")
                        h_n -= 1

                # 为新医院生成救护车
                for i in range(h_before + 1, h_n + 1):
                    async with db.execute("SELECT lat, lng FROM hospitals WHERE id=?", (f"H{i}",)) as cur:
                        row = await cur.fetchone()
                    if row:
                        for _ in range(random.choice([1, 2])):
                            a_n += 1
                            try:
                                await db.execute(
                                    "INSERT OR IGNORE INTO ambulances (id,status,lat,lng,hospital_id) VALUES (?,?,?,?,?)",
                                    (f"A{a_n}", "待命",
                                     row[0] + random.uniform(-0.0002, 0.0002),
                                     row[1] + random.uniform(-0.0002, 0.0002),
                                     f"H{i}"),
                                )
                            except Exception:
                                a_n -= 1
                await asyncio.sleep(0.3)
            await db.commit()
            print(f"    → 新增 {added} 家")
            h_total += added

        # ── 消防站 ────────────────────────────────────────────────────────────
        print("\n=== 导入消防站数据 ===")
        fs_n = await _max_id(db, "fire_stations", "FS")
        async with db.execute("SELECT name, lat, lng FROM fire_stations") as cur:
            seen_fs = {f"{r[0]}|{r[1]:.3f}|{r[2]:.3f}" for r in await cur.fetchall()}
        fs_total = 0

        for dist in _DISTRICTS:
            print(f"\n  [{dist}] 搜索消防站...")
            pois = await _fetch(client, "消防救援站|消防大队", dist)
            await asyncio.sleep(0.3)
            added = 0
            for poi in pois:
                name = poi.get("name", "").strip()
                coords = _parse_loc(poi.get("location", ""))
                if not coords:
                    continue
                lat, lng = coords
                key = f"{name}|{lat:.3f}|{lng:.3f}"
                if key in seen_fs:
                    continue
                seen_fs.add(key)
                fs_n += 1
                address = poi.get("address") or f"上海{name}"
                try:
                    await db.execute(
                        "INSERT OR IGNORE INTO fire_stations (id,name,address,lat,lng,total_trucks,available_trucks,personnel) VALUES (?,?,?,?,?,?,?,?)",
                        (f"FS{fs_n}", name, address, lat, lng, 4, 4, 20),
                    )
                    added += 1
                except Exception as e:
                    print(f"  [失败] {name}: {e}")
                    fs_n -= 1
            await db.commit()
            print(f"    → 新增 {added} 个")
            fs_total += added

    print(f"\n导入完成：医院新增 {h_total}（总计 {h_n}），救护车总计 {a_n}，消防站新增 {fs_total}（总计 {fs_n}）")


if __name__ == "__main__":
    asyncio.run(run())
