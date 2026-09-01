"""把最新資料寫回圖鑑頁的內嵌快照。

圖鑑頁線上時向 ./data/ 取資料，但在無法取用的情境（直接開啟檔案、
發布成 Artifact 預覽）會退回頁面裡的 SNAPSHOT 常數。這份常數若不同步，
預覽看到的就是舊數字——實測曾停在六天前的資料而不自知。

由管線在產出後自動呼叫，不需手動維護。

用法：
    python3 tools/embed_snapshot.py [代號]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "site" / "index.html"
DATA = ROOT / "site" / "data"
BEGIN = "const SNAPSHOT = "
END = "\n};\n"


def build(symbol: str) -> dict:
    index = json.loads((DATA / "index.json").read_text())
    species_doc = json.loads((DATA / "species.json").read_text())
    daily_doc = json.loads((DATA / index["files"]["daily"]["path"]).read_text())
    balance = json.loads((DATA / "balance.json").read_text())

    species = next(s for s in species_doc["species"] if s["symbol"] == symbol)
    daily = next(m for m in daily_doc["monsters"] if m["symbol"] == symbol)
    return {
        "meta": {
            "generation": species_doc["generation"],
            "season": species_doc["season"],
            "market_date": daily_doc["market_date"],
            "settled_at": daily_doc["settled_at"],
            "fiscal_period": species_doc.get("fiscal_period"),
            "stats_as_of": species_doc.get("stats_as_of"),
        },
        "balance": {"status": balance.get("status", "draft")},
        "species": species,
        "daily": daily,
    }


def main(symbol: str = "2330") -> int:
    html = PAGE.read_text()
    start = html.index(BEGIN)
    end = html.index(END, start) + len(END)

    snapshot = json.dumps(build(symbol), ensure_ascii=False, indent=2)
    replacement = f"{BEGIN}{snapshot};\n"
    updated = html[:start] + replacement + html[end:]

    if updated == html:
        print("內嵌快照已是最新")
        return 0
    PAGE.write_text(updated)
    data = build(symbol)
    print(f"已更新內嵌快照：{symbol} {data['species']['name']} "
          f"／{data['meta']['market_date']}／收盤 {data['daily']['price']['close']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "2330"))
