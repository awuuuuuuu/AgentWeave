"""
应急物资仓库 MCP Server

工具：
- get_inventory: 查询库存（可按类别过滤）
- allocate_standard_pack: 按标准调拨包扣减（Ⅲ级/Ⅱ级预案）
- allocate_custom: 自定义扣减物资
- check_alerts: 查询低于预警线的物资

标准调拨包（天津滨海新区应急物资储备清单）：
  Ⅲ级：防毒面具10 + 防化服5 + 空气呼吸器5 + 急救箱10
  Ⅱ级：防毒面具20 + 防化服10 + 空气呼吸器10 + 急救箱20 + 医用氧气瓶10
"""
from __future__ import annotations

from pathlib import Path
import aiosqlite
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("warehouse", host="0.0.0.0", port=8104)

DB = Path(__file__).parent.parent.parent / "city_state.db"

_STANDARD_PACKS: dict[str, list[dict]] = {
    "Ⅲ": [
        {"name": "防毒面具A级", "qty": 10},
        {"name": "轻型防化服",  "qty": 5},
        {"name": "空气呼吸器",  "qty": 5},
        {"name": "急救箱",      "qty": 10},
    ],
    "Ⅱ": [
        {"name": "防毒面具A级", "qty": 20},
        {"name": "重型防化服",  "qty": 10},
        {"name": "空气呼吸器",  "qty": 10},
        {"name": "急救箱",      "qty": 20},
        {"name": "医用氧气瓶",  "qty": 10},
    ],
}


@mcp.tool()
async def get_inventory(category: str | None = None) -> list[dict]:
    """查询应急物资库存。

    Args:
        category: 可选类别过滤（如"个人防护"/"医疗物资"等），None 返回全部

    Returns:
        库存列表，每项包含 id/name/category/quantity/unit/alert_threshold
    """
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        if category:
            cur = await db.execute(
                "SELECT * FROM warehouse_inventory WHERE category = ? ORDER BY name",
                (category,),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM warehouse_inventory ORDER BY category, name"
            )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


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
    Ⅲ级包：防毒面具×10、防化服×5、空气呼吸器×5、急救箱×10。
    Ⅱ级包：防毒面具×20、防化服×10、空气呼吸器×10、急救箱×20、氧气瓶×10。

    Args:
        level: 预案等级，"Ⅲ" 或 "Ⅱ"

    Returns:
        调拨汇总，包含 level/items（各物资状态）/success
    """
    if level not in _STANDARD_PACKS:
        raise ValueError(f"预案等级 {level!r} 无效，可选：Ⅲ、Ⅱ")

    pack = _STANDARD_PACKS[level]
    async with aiosqlite.connect(DB) as db:
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

    async with aiosqlite.connect(DB) as db:
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
    async with aiosqlite.connect(DB) as db:
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


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
