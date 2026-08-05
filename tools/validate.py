"""驗證圖鑑資料檔是否符合 Schema。

除了 JSON Schema 的結構檢查，另加幾條 Schema 表達不了的跨欄位規則
（例如六圍總和必須等於該稀有度的 BST）。

用法：
    python3 tools/validate.py            # 驗證 examples/
    python3 tools/validate.py <資料目錄>
"""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"

PAIRS = [
    ("index.json", "index.schema.json"),
    ("species.json", "species.schema.json"),
    ("balance.json", "balance.schema.json"),
]


def load(path: Path):
    return json.loads(path.read_text())


def check_schema(data_path: Path, schema_path: Path, errors: list) -> None:
    validator = Draft202012Validator(load(schema_path))
    found = sorted(validator.iter_errors(load(data_path)), key=lambda e: list(e.path))
    if found:
        for e in found:
            loc = "/".join(str(p) for p in e.path) or "(root)"
            errors.append(f"{data_path.name} → {loc}: {e.message}")
    else:
        print(f"  ✅ {data_path.name} 符合 {schema_path.name}")


def check_cross_rules(root: Path, errors: list) -> None:
    """Schema 表達不了的跨檔／跨欄位規則。"""
    species_doc = load(root / "species.json")
    balance = load(root / "balance.json")
    tiers = balance["rarity_tiers"]

    known = set()
    for sp in species_doc["species"]:
        known.add(sp["symbol"])

        # 六圍總和必須等於稀有度的 BST（面積由階層決定，同階只差形狀）
        total = sum(sp["base_stats"].values())
        if total != sp["bst"]:
            errors.append(f"{sp['symbol']} 六圍總和 {total} ≠ bst {sp['bst']}")

        # BST 必須與該稀有度階層一致
        tier_bst = tiers[sp["rarity"]]["bst"]
        if sp["bst"] != tier_bst:
            errors.append(
                f"{sp['symbol']} bst {sp['bst']} 與稀有度 {sp['rarity']} 的 {tier_bst} 不符")

        # 招式解鎖等級不可超過等級上限
        max_level = balance.get("leveling", {}).get("max_level")
        for mv in sp.get("move_pool", []):
            if max_level and mv["learn_level"] > max_level:
                errors.append(
                    f"{sp['symbol']} 招式 {mv['id']} 解鎖等級 {mv['learn_level']} 超過上限 {max_level}")

    # 遭遇基礎率總和應為 1
    total_rate = sum(t["encounter_base_rate"] for t in tiers.values())
    if abs(total_rate - 1.0) > 1e-9:
        errors.append(f"稀有度遭遇基礎率總和 {total_rate} ≠ 1.0")

    # 每日檔的每一隻都必須在種族檔裡有定義
    for daily_path in sorted((root / "daily").glob("*.json")):
        daily = load(daily_path)
        for m in daily["monsters"]:
            if m["symbol"] not in known:
                errors.append(f"{daily_path.name} 的 {m['symbol']} 在 species.json 中無定義")

        # 淨值盾階層必須與 PB 一致
        for m in daily["monsters"]:
            pb = m["valuation"].get("pb")
            tier = m["net_value_shield"]["tier"]
            if pb is None:
                continue
            cfg = balance["net_value_shield"]
            expect = ("gold" if pb < cfg["gold_threshold"]
                      else "silver" if pb < cfg["pb_threshold"]
                      else "none")
            if tier != expect:
                errors.append(
                    f"{daily_path.name} {m['symbol']} PB {pb} 應為 {expect} 盾，實為 {tier}")
        print(f"  ✅ {daily_path.name} 通過每日層交叉檢查")


def main(root: Path) -> int:
    errors: list = []
    print(f"驗證 {root}/")
    for data_name, schema_name in PAIRS:
        check_schema(root / data_name, SCHEMA_DIR / schema_name, errors)

    daily_schema = SCHEMA_DIR / "daily.schema.json"
    for daily_path in sorted((root / "daily").glob("*.json")):
        check_schema(daily_path, daily_schema, errors)

    check_cross_rules(root, errors)

    if errors:
        print(f"\n❌ {len(errors)} 個問題：")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\n✅ 全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "examples")))
