"""從 data/spec.json 與 data/balance.json 生成規格現況文件。

為什麼要生成而不是手寫：手寫的規格書一定會跟程式漂移。實測曾發生設計文件
寫「魔防僅取波動率」、程式跑的卻是「殖利率＋波動率」的複合版，差了一個多月
沒人發現。數值由 spec.json 的 values 指向 balance.json 的路徑，於生成時代入，
文件因此永遠等於程式實際讀的那份設定。

分工：
  Notion《設計討論總結》  討論與決策理由，append-only，保留歷史
  data/spec.json          規格現況，每項只出現一次，會被覆寫
  data/balance.json       數值的單一來源，程式與文件共用
  docs/spec-current.md    本工具的產出，貼進 Notion 或由 Pages 呈現

用法：
    python3 tools/gen_spec.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "data" / "spec.json"
BALANCE = ROOT / "data" / "balance.json"
OUT = ROOT / "docs" / "spec-current.md"

BADGE = {
    "locked": "🔒 定案",
    "playtest": "🧪 測試中",
    "draft": "📝 提案",
    "undecided": "❓ 待決策",
}


def resolve(balance: dict, path: str):
    """解析 'rarity_tiers.*.bst' 這類路徑，`*` 代表展開該層所有鍵。"""
    parts = path.split(".")
    node = balance
    for i, part in enumerate(parts):
        if part == "*":
            rest = ".".join(parts[i + 1:])
            return {k: (resolve(v, rest) if rest else v) for k, v in node.items()}
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def fmt(value) -> str:
    """把解析出的數值排成一行可讀的文字。"""
    if value is None:
        return "—"
    if isinstance(value, dict):
        return "、".join(f"{k} {fmt(v)}" for k, v in value.items())
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return "、".join(str(v.get("name") or v.get("id") or v) for v in value)
        return "、".join(str(v) for v in value)
    return str(value)


def render(spec: dict, balance: dict) -> str:
    lines = [
        "# 規格現況",
        "",
        "> **本檔由 `tools/gen_spec.py` 生成，請勿手動編輯。**",
        f"> 規格版本 {spec['spec_version']}　·　更新於 {spec['updated_on']}",
        f"　·　數值取自 `data/balance.json`（狀態：{balance.get('status', '未標示')}）",
        "",
        "這份文件只描述「**現在**是什麼」，每一項只出現一次——結構上不會有兩個版本互相矛盾。",
        "決策的來龍去脈保留在 Notion《設計討論總結》，那份是 append-only 的討論紀錄。",
        "",
        "| 標記 | 意義 |",
        "|---|---|",
    ]
    for key, label in BADGE.items():
        lines.append(f"| {label} | {spec['status_legend'][key]} |")
    lines.append("")

    by_category: dict[str, list[dict]] = {}
    for entry in spec["entries"]:
        by_category.setdefault(entry["category"], []).append(entry)

    counts = {k: len(v) for k, v in by_category.items()}
    lines += ["## 總覽", "",
              "　".join(f"**{k}** {v} 項" for k, v in counts.items()), ""]

    undecided = [e for e in spec["entries"] if e["status"] == "undecided"]
    if undecided:
        lines += ["> ❓ **目前有 "
                  f"{len(undecided)} 項待你決策**："
                  + "、".join(e["name"] for e in undecided), ""]

    for category, entries in by_category.items():
        lines += [f"## {category}", ""]
        for e in entries:
            lines.append(f"### {e['name']}　{BADGE[e['status']]}")
            lines.append("")
            lines.append(e["definition"])
            lines.append("")

            rows = []
            if e.get("sources"):
                rows.append(("資料來源", "、".join(e["sources"])))
            if e.get("values"):
                for label, path in e["values"].items():
                    rows.append((label, f"`{fmt(resolve(balance, path))}`"))
            if e.get("implemented_in"):
                rows.append(("實作位置", f"`{e['implemented_in']}`"))
            if e.get("validation"):
                rows.append(("驗證", e["validation"]))
            if e.get("decided_on"):
                rows.append(("定案日", e["decided_on"]))
            if e.get("supersedes"):
                rows.append(("取代", e["supersedes"]))
            if e.get("scope"):
                rows.append(("系統邊界", e["scope"]))
            if e.get("rationale"):
                rows.append(("理由", e["rationale"]))
            if e.get("options"):
                rows.append(("選項", "／".join(e["options"])))
            if e.get("caveat"):
                rows.append(("⚠️ 注意", e["caveat"]))

            if rows:
                lines += ["| | |", "|---|---|"]
                lines += [f"| {k} | {v} |" for k, v in rows]
                lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    spec = json.loads(SPEC.read_text())
    balance = json.loads(BALANCE.read_text())

    missing = [f"{e['id']}.{label} → {path}"
               for e in spec["entries"]
               for label, path in (e.get("values") or {}).items()
               if resolve(balance, path) is None]
    if missing:
        print("⚠️ 以下數值路徑在 balance.json 中找不到：")
        for m in missing:
            print(f"   {m}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(spec, balance))

    counts: dict[str, int] = {}
    for e in spec["entries"]:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    print(f"已生成 {OUT}（{len(spec['entries'])} 項）")
    print("   " + "、".join(f"{BADGE[k]} {v}" for k, v in counts.items()))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
