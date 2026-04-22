"""
将旧版 Collection（无 BM25 字段）迁移到当前 schema（含 sparse_vector）。

背景：
    Milvus 不支持 ALTER COLLECTION，无法直接新增字段。
    迁移流程：导出旧数据 → 删除旧 collection → 新 schema 重建 → 重新写入。

警告：
    此操作会删除旧 collection，数据在导出后写入前存在丢失风险。
    执行前务必确认：
      1. Milvus 有足够磁盘空间存储导出的临时数据
      2. 迁移期间暂停所有写入请求

运行：
    uv run python scripts/migrate_add_bm25.py --dry-run   # 预检，不实际执行
    uv run python scripts/migrate_add_bm25.py             # 执行迁移
    uv run python scripts/migrate_add_bm25.py --uri http://localhost:19530 --collection ragent_chunks
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from ingestion.store.milvus_store import (
    MilvusStoreConfig,
    MilvusStore,
    _REQUIRED_FIELDS_BM25,
    _F_CHUNK_ID,
)

_EXPORT_PAGE_SIZE = 500


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移 Milvus Collection 到 BM25 schema")
    parser.add_argument("--uri", default="http://localhost:19530")
    parser.add_argument("--collection", default="ragent_chunks")
    parser.add_argument("--dim", type=int, default=1536)
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不执行迁移")
    args = parser.parse_args()

    from pymilvus import MilvusClient
    client = MilvusClient(uri=args.uri)

    # ── 前置检查 ───────────────────────────────────────────────────────────────
    if not client.has_collection(args.collection):
        print(f"[ERROR] Collection '{args.collection}' 不存在，请先运行 init_collection.py。")
        sys.exit(1)

    desc = client.describe_collection(args.collection)
    existing = {f["name"] for f in desc["fields"]}
    missing = _REQUIRED_FIELDS_BM25 - existing

    if not missing:
        print(f"[OK] Collection '{args.collection}' schema 已是最新，无需迁移。")
        return

    print(f"[INFO] 缺少字段：{missing}")

    if args.dry_run:
        print("[DRY-RUN] 预检完成，未执行任何操作。去掉 --dry-run 后执行实际迁移。")
        return

    # ── 确认 ──────────────────────────────────────────────────────────────────
    confirm = input(
        f"\n即将删除 collection '{args.collection}' 并重建。\n"
        "迁移期间数据不可用，完成后需重新 embed 所有文本（BM25 索引由 Milvus 自动重建）。\n"
        "输入 YES 继续：\n> "
    ).strip()
    if confirm != "YES":
        print("已取消。")
        sys.exit(0)

    # ── 导出旧数据 ────────────────────────────────────────────────────────────
    print("[1/4] 导出旧数据...")
    output_fields = list(existing - {_F_CHUNK_ID})  # primary key 会自动包含
    all_rows: list[dict] = []
    offset = 0
    while True:
        rows = client.query(
            collection_name=args.collection,
            filter="",
            output_fields=output_fields,
            limit=_EXPORT_PAGE_SIZE,
            offset=offset,
            consistency_level="Strong",
        )
        if not rows:
            break
        all_rows.extend(rows)
        offset += len(rows)
        print(f"  已导出 {offset} 条...", end="\r")

    print(f"\n  共导出 {len(all_rows)} 条。")

    if not all_rows:
        print("[WARN] 旧 collection 为空，直接重建。")

    # ── 删除旧 collection ──────────────────────────────────────────────────────
    print("[2/4] 删除旧 collection...")
    client.drop_collection(args.collection)
    print("  完成。")

    # ── 重建 collection ────────────────────────────────────────────────────────
    print("[3/4] 以新 schema 重建 collection...")
    cfg = MilvusStoreConfig(
        uri=args.uri,
        collection_name=args.collection,
        vector_dim=args.dim,
        enable_bm25=True,
    )
    MilvusStore(config=cfg, client=client)
    print("  完成。")

    # ── 写回数据 ───────────────────────────────────────────────────────────────
    # 注意：旧数据没有 sparse_vector，Milvus BM25 Function 会在写入时自动从 text 重新生成。
    # 但旧行里 text 字段必须存在，否则无法生成稀疏向量。
    if all_rows:
        print(f"[4/4] 写回 {len(all_rows)} 条数据...")
        for start in range(0, len(all_rows), _EXPORT_PAGE_SIZE):
            batch = all_rows[start : start + _EXPORT_PAGE_SIZE]
            client.upsert(collection_name=args.collection, data=batch)
            print(f"  已写入 {min(start + _EXPORT_PAGE_SIZE, len(all_rows))} / {len(all_rows)} 条...", end="\r")
        print()
    else:
        print("[4/4] 无数据需要写回，跳过。")

    print(f"\n[OK] 迁移完成。Collection '{args.collection}' 已升级到 BM25 schema。")


if __name__ == "__main__":
    main()
