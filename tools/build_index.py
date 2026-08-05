"""產生圖鑑索引（manifest）。

遊戲端每日只先抓這個小檔，比對 market_date 與各檔 sha256，
決定哪些檔案需要重新下載——種族層通常整季不變，不必每天重抓。

用法：
    python3 tools/build_index.py <資料目錄>
    python3 tools/build_index.py examples
"""

import hashlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))
SCHEMA_VERSION = "0.1.0"
SETTLEMENT_HOUR = 19  # 每日結算時刻（台北時間 19:00）


def file_ref(root: Path, rel: str) -> dict:
    path = root / rel
    data = path.read_bytes()
    return {
        "path": rel,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, TZ).isoformat(),
    }


def next_settlement(market_date: str) -> str:
    """下一個結算時刻。此處僅推進一日；正式管線應改查交易日曆跳過休市日。"""
    d = datetime.strptime(market_date, "%Y-%m-%d").replace(
        hour=SETTLEMENT_HOUR, tzinfo=TZ)
    return (d + timedelta(days=1)).isoformat()


def main(root: Path) -> None:
    daily = json.loads((root / "daily/2026-08-05.json").read_text())
    species = json.loads((root / "species.json").read_text())
    market_date = daily["market_date"]

    index = {
        "schema_version": SCHEMA_VERSION,
        "generation": species["generation"],
        "season": species["season"],
        "market_date": market_date,
        "generated_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        "next_settlement_at": next_settlement(market_date),
        "files": {
            "species": file_ref(root, "species.json"),
            "daily": file_ref(root, f"daily/{market_date}.json"),
            "balance": file_ref(root, "balance.json"),
        },
        "pipeline": {
            "sources": [
                {"name": "fugle-snapshot-tse", "endpoint": "/marketdata/v1.0/snapshot/quotes/TSE", "record_count": 1536},
                {"name": "fugle-historical-candles", "endpoint": "/marketdata/v1.0/stock/historical/candles", "record_count": 282},
                {"name": "fugle-index", "endpoint": "/marketdata/v1.0/stock/intraday/quote/IX0001", "record_count": 1},
                {"name": "twse-bwibbu", "endpoint": "/v1/exchangeReport/BWIBBU_ALL", "record_count": 1082},
                {"name": "twse-monthly-revenue", "endpoint": "/v1/opendata/t187ap05_L", "record_count": 1082},
                {"name": "twse-turnover", "endpoint": "/v1/exchangeReport/FMSRFK_ALL", "record_count": 29253},
            ],
            "warnings": [
                "2026Q2 財報尚未到公布期限，t187ap06/t187ap07 僅 4 筆；本包 EPS 由收盤價÷本益比回推、每股淨值由收盤價÷股價淨值比回推",
                "market_cap 待補：需股本資料，來源同上受阻",
                "ma60 未計算：本次回補僅取 5/20/240 日",
            ],
        },
    }

    out = root / "index.json"
    out.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    print(f"已寫入 {out}")
    for name, ref in index["files"].items():
        print(f"  {name:<8} {ref['bytes']:>7} bytes  {ref['sha256'][:16]}…")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "examples"))
