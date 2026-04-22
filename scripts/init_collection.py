"""
初始化 Milvus Collection（含完整 BM25 schema）。

用途：全新环境第一次建库时使用，不会覆盖已有数据。
若 collection 已存在且 schema 完整，脚本直接退出，不做任何操作。

运行：
    uv run python scripts/init_collection.py
    uv run python scripts/init_collection.py --uri http://localhost:19530 --collection ragent_chunks
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig, _REQUIRED_FIELDS_BM25


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 Milvus Collection")
    parser.add_argument("--uri", default="http://localhost:19530")
    parser.add_argument("--collection", default="ragent_chunks")
    parser.add_argument("--dim", type=int, default=1536)
    parser.add_argument("--no-bm25", action="store_true", help="禁用 BM25 Function（Milvus < 2.5 时使用）")
    args = parser.parse_args()

    cfg = MilvusStoreConfig(
        uri=args.uri,
        collection_name=args.collection,
        vector_dim=args.dim,
        enable_bm25=not args.no_bm25,
    )

    from pymilvus import MilvusClient
    client = MilvusClient(uri=cfg.uri)

    if client.has_collection(cfg.collection_name):
        desc = client.describe_collection(cfg.collection_name)
        existing = {f["name"] for f in desc["fields"]}
        if _REQUIRED_FIELDS_BM25.issubset(existing):
            print(f"[OK] Collection '{cfg.collection_name}' 已存在且 schema 完整，无需操作。")
            return
        print(f"[WARN] Collection '{cfg.collection_name}' 已存在但 schema 过期。")
        print("       请改用 scripts/migrate_add_bm25.py 进行迁移。")
        sys.exit(1)

    MilvusStore(config=cfg, client=client)
    print(f"[OK] Collection '{cfg.collection_name}' 创建成功（BM25={'启用' if cfg.enable_bm25 else '禁用'}）。")


if __name__ == "__main__":
    main()
