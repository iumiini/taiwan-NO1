"""產生圖鑑索引（manifest）。

遊戲端每日只先抓這個小檔，比對 market_date 與各檔 sha256，決定哪些檔案
需要重新下載——種族層通常整季不變，不必每天重抓。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
SCHEMA_VERSION = "0.1.0"
SETTLEMENT_HOUR = 19


def file_ref(root: Path, rel: str) -> dict:
    data = (root / rel).read_bytes()
    return {
        "path": rel,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "updated_at": datetime.fromtimestamp((root / rel).stat().st_mtime, TZ).isoformat(),
    }


def next_settlement(market_date: str) -> str:
    """下一個結算時刻。僅推進一日；正式排程應改查交易日曆跳過休市日。"""
    d = datetime.strptime(market_date, "%Y-%m-%d").replace(
        hour=SETTLEMENT_HOUR, tzinfo=TZ)
    return (d + timedelta(days=1)).isoformat()


def build_index(root: Path, market_date: str, species_doc: dict,
                sources: list[dict] | None = None,
                warnings: list[str] | None = None) -> Path:
    index = {
        "schema_version": SCHEMA_VERSION,
        "generation": species_doc["generation"],
        "season": species_doc["season"],
        "market_date": market_date,
        "generated_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        "next_settlement_at": next_settlement(market_date),
        "files": {
            "species": file_ref(root, "species.json"),
            "daily": file_ref(root, f"daily/{market_date}.json"),
            "balance": file_ref(root, "balance.json"),
        },
    }
    pipeline = {}
    if sources:
        pipeline["sources"] = sources
    if warnings:
        pipeline["warnings"] = warnings
    if pipeline:
        index["pipeline"] = pipeline

    path = root / "index.json"
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    return path
