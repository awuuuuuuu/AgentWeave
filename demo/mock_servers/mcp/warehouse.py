"""
应急物资仓库 MCP Server

工具：
- get_inventory: 查询库存（可按类别过滤）
- allocate_standard_pack: 按标准调拨包扣减（Ⅲ级/Ⅱ级预案）
- allocate_custom: 自定义扣减物资
- check_alerts: 查询低于预警线的物资

标准调拨包（上海市浦东新区应急物资储备中心）：
  Ⅲ级：防护口罩50 + 急救箱10 + 隔离警戒带20 + 通信对讲机10
  Ⅱ级：防护口罩100 + 防化服10 + 空气呼吸器8 + 急救箱20 + 医用氧气瓶10 + 消防水带10
"""
from __future__ import annotations

from pathlib import Path
import aiosqlite
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("warehouse", host="0.0.0.0", port=8104)

DB = Path(__file__).parent.parent.parent / "city_state.db"

_STANDARD_PACKS: dict[str, list[dict]] = {
    "Ⅲ": [
        {"name": "防护口罩N95", "qty": 50},
        {"name": "急救箱",      "qty": 10},
        {"name": "隔离警戒带",  "qty": 20},
        {"name": "通信对讲机",  "qty": 10},
    ],
    "Ⅱ": [
        {"name": "防护口罩N95", "qty": 100},
        {"name": "防化服",      "qty": 10},
        {"name": "空气呼吸器",  "qty": 8},
        {"name": "急救箱",      "qty": 20},
        {"name": "医用氧气瓶",  "qty": 10},
        {"name": "消防水带",    "qty": 10},
    ],
}


@mcp.tool()
async def get_inventory(category: str | None = None, warehouse_id: str | None = None) -> dict:
    """查询应急物资库存。

    Args:
        category: 可选类别过滤，必须使用精确类别名（如"消防器材"/"个人防护"/"医疗物资"/"现场处置"/"通信设备"/"动力设备"/"后勤物资"/"洗消物资"/"防汛物资"/"电力抢修"），None 返回全部类别
        warehouse_id: 可选仓库 ID 过滤（如"WH1"/"WH2"），None 返回最近仓库

    Returns:
        {warehouse: {name, lat, lng, address}, items: [...库存列表，每项含 id/name/category/quantity/unit/alert_threshold...]}
    """
    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        # 查询仓库信息
        if warehouse_id:
            wh_row = await (await db.execute(
                "SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)
            )).fetchone()
        else:
            wh_row = await (await db.execute("SELECT * FROM warehouses LIMIT 1")).fetchone()
        warehouse = dict(wh_row) if wh_row else {}

        # 未指定 warehouse_id 时，默认锁定到上面查到的第一个仓库，避免返回全部 8 仓数据
        effective_wh_id = warehouse_id or (warehouse.get("id") if warehouse else None)

        # 查询库存
        conditions = []
        params = []
        if category:
            conditions.append("category = ?")
            params.append(category)
        if effective_wh_id:
            conditions.append("warehouse_id = ?")
            params.append(effective_wh_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        cur = await db.execute(
            f"SELECT * FROM warehouse_inventory {where} ORDER BY category, name",
            params,
        )
        rows = await cur.fetchall()
    if warehouse:
        warehouse["map_marker"] = {
            "icon": "🏭",
            "position": [warehouse["lng"], warehouse["lat"]],
            "label": warehouse["name"],
        }
    return {
        "warehouse": warehouse,
        "items": [dict(r) for r in rows],
    }


async def _deduct_items(db: aiosqlite.Connection, items: list[dict]) -> list[dict]:
    """内部：按 items 列表扣减库存，返回每项的扣减结果"""
    results = []
    for item in items:
        name = item["name"]
        qty = item["qty"]
        cur = await db.execute(
            "SELECT id, quantity FROM warehouse_inventory WHERE name = ?", (name,)
        )
        row = await cur.fetchone()
        if not row:
            results.append({"name": name, "requested": qty, "status": "未找到", "remaining": None})
            continue
        rid, current_qty = row[0], row[1]
        if current_qty < qty:
            results.append({
                "name": name,
                "requested": qty,
                "status": f"库存不足（当前 {current_qty}）",
                "remaining": current_qty,
            })
            continue
        new_qty = current_qty - qty
        await db.execute(
            "UPDATE warehouse_inventory SET quantity = ? WHERE id = ?", (new_qty, rid)
        )
        results.append({
            "name": name,
            "requested": qty,
            "status": "已调拨",
            "remaining": new_qty,
        })
    return results


@mcp.tool()
async def allocate_standard_pack(level: str) -> dict:
    """⚠️ 写操作：按标准调拨包扣减库存，需经 HITL 审批后执行。
    Ⅲ级包：防护口罩×50、急救箱×10、隔离警戒带×20、通信对讲机×10。
    Ⅱ级包：防护口罩×100、防化服×10、空气呼吸器×8、急救箱×20、氧气瓶×10、消防水带×10。

    Args:
        level: 预案等级，"Ⅲ" 或 "Ⅱ"

    Returns:
        调拨汇总，包含 level/items（各物资状态）/success
    """
    if level not in _STANDARD_PACKS:
        raise ValueError(f"预案等级 {level!r} 无效，可选：Ⅲ、Ⅱ")

    pack = _STANDARD_PACKS[level]
    async with aiosqlite.connect(DB, timeout=30) as db:
        results = await _deduct_items(db, pack)
        await db.commit()

    failed = [r for r in results if r["status"] != "已调拨"]
    return {
        "level": level,
        "items": results,
        "success": len(failed) == 0,
        "message": (
            f"{level} 级标准调拨包已发放完成"
            if not failed
            else f"调拨完成，但以下物资异常：{[f['name'] for f in failed]}"
        ),
    }


@mcp.tool()
async def allocate_custom(items: list[dict]) -> dict:
    """⚠️ 写操作：自定义扣减物资库存，需经 HITL 审批后执行。

    Args:
        items: 物资列表，每项格式为 {"name": "物资名称", "qty": 数量}

    Returns:
        调拨汇总，包含 items/success/message
    """
    if not items:
        raise ValueError("调拨物资列表不能为空")

    async with aiosqlite.connect(DB, timeout=30) as db:
        results = await _deduct_items(db, items)
        await db.commit()

    failed = [r for r in results if r["status"] != "已调拨"]
    return {
        "items": results,
        "success": len(failed) == 0,
        "message": (
            f"自定义调拨完成，共 {len(items)} 项"
            if not failed
            else f"调拨完成，但以下物资异常：{[f['name'] for f in failed]}"
        ),
    }


@mcp.tool()
async def check_alerts() -> list[dict]:
    """查询库存低于预警线的物资。

    Returns:
        告警物资列表，每项包含 name/category/quantity/alert_threshold/shortage
    """
    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT name, category, quantity, unit, alert_threshold
               FROM warehouse_inventory
               WHERE quantity <= alert_threshold
               ORDER BY (quantity * 1.0 / alert_threshold) ASC"""
        )
        rows = await cur.fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["shortage"] = d["alert_threshold"] - d["quantity"]
        result.append(d)
    return result


@mcp.tool()
async def list_warehouses(
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 10.0,
) -> list[dict]:
    """查询应急物资仓库列表，按距离升序排列。优先返回 10km 内仓库；无结果时返回全部。

    Args:
        lat: 参考点纬度（事故坐标），None 则返回全部仓库
        lng: 参考点经度，None 则返回全部仓库
        radius_km: 搜索半径（公里），默认 10km；10km 内无仓库时自动扩大到全部

    Returns:
        仓库列表，每项包含 id/name/address/lat/lng/distance_km/map_marker
    """
    import math

    def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lng2 - lng1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    async with aiosqlite.connect(DB, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM warehouses ORDER BY id")
        rows = await cur.fetchall()

    all_wh = [dict(row) for row in rows]

    def _build(radius: float | None) -> list[dict]:
        out = []
        for wh in all_wh:
            if lat is not None and lng is not None:
                dist = _haversine(lat, lng, wh["lat"], wh["lng"])
                if radius is not None and dist > radius:
                    continue
                wh = {**wh, "distance_km": round(dist, 1)}
            else:
                wh = {**wh, "distance_km": None}
            wh["map_marker"] = {
                "icon": "🏭",
                "position": [wh["lng"], wh["lat"]],
                "label": wh["name"],
            }
            out.append(wh)
        return out

    result = _build(radius_km)

    # 10km 内无仓库时自动扩大到全部
    if lat is not None and lng is not None and not result:
        result = _build(None)

    if lat is not None and lng is not None:
        result.sort(key=lambda x: x["distance_km"])
    return result


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
