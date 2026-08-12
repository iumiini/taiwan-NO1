"""StatMapper：把財務與行情資料換算成怪獸。

設計文件第十章的能力值體系：

    面積（市值／BST）→ 形狀（六數據）→ 烙印（個體值）→ 成長（等級）→ 天氣（當日狀態）

本模組只負責前兩層——面積與形狀，也就是種族值。烙印與成長屬玩家存檔，
天氣屬每日層，都不在這裡。

兩個關鍵性質：

1. **純函式**。同輸入必得同輸出，不讀時鐘、不碰網路。平衡調整只改
   balance.json 的參數與此處的映射，不動遊戲邏輯。
2. **面積由階層決定**。六圍總和恆等於該稀有度的 BST，同階怪獸面積相同、
   只差形狀——形狀是風格不是強弱。

正規化採百分位排名而非絕對值，解決台積電絕對值爆表的問題；級距為 10 等，
直接對映百分位（每 10 百分位一級）。
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 六圍 ← 資料來源。每項為 (欄位, 是否反向) 的清單，多項時取百分位平均。
# 反向代表數值越低能力越高。
STAT_SOURCES: dict[str, list[tuple[str, bool]]] = {
    "hp":   [("revenue", False)],          # 生命 ← 營收規模（體格）
    "atk":  [("eps", False)],              # 物攻 ← EPS（真金白銀的獲利）
    "matk": [("revenue_yoy", False)],      # 魔攻 ← 營收 YoY（題材與想像）
    "def":  [("debt_ratio", True)],        # 物防 ← 負債比（反向）
    "mdef": [("volatility_60d", True),     # 魔防 ← 波動率（反向）＋殖利率
             ("dividend_yield", False)],
    "spd":  [("turnover_ratio", False)],   # 速度 ← 週轉率（股性活潑）
}

GRADE_LEVELS = 10  # 級距＝10 等，每 10 百分位一級


def load_json(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text())


def percentile_ranks(values: dict[str, float | None]) -> dict[str, float]:
    """在名單內取百分位（0~100）。缺值給 50，避免單一欄位缺漏毀掉整隻怪。

    同分者共用平均名次，否則大量同值（例如週轉率相同）會被任意排序。
    """
    present = sorted((v, s) for s, v in values.items() if v is not None)
    n = len(present)
    if n == 0:
        return {s: 50.0 for s in values}

    ranks: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[j + 1][0] == present[i][0]:
            j += 1
        avg_index = (i + j) / 2
        pct = 100.0 if n == 1 else avg_index / (n - 1) * 100
        for k in range(i, j + 1):
            ranks[present[k][1]] = pct
        i = j + 1

    for symbol, value in values.items():
        if value is None:
            ranks[symbol] = 50.0
    return ranks


def grade(pct: float) -> int:
    """百分位 → 1~10 級。"""
    return max(1, min(GRADE_LEVELS, int(pct // GRADE_LEVELS) + 1))


def allocate(grades: dict[str, int], bst: int) -> dict[str, int]:
    """把 BST 依級距比例分配到六圍，總和嚴格等於 BST。

    先取整、再把餘數依小數部分由大到小補回，確保不因四捨五入而失衡。
    """
    total = sum(grades.values())
    raw = {k: bst * g / total for k, g in grades.items()}
    out = {k: max(1, int(v)) for k, v in raw.items()}

    remainder = bst - sum(out.values())
    order = sorted(raw, key=lambda k: raw[k] - int(raw[k]), reverse=True)
    i = 0
    while remainder != 0 and order:
        key = order[i % len(order)]
        step = 1 if remainder > 0 else -1
        if out[key] + step >= 1:
            out[key] += step
            remainder -= step
        i += 1
    return out


def assign_rarity(ranked_symbols: list[str], tiers: dict) -> dict[str, str]:
    """依世代內市值排名切出金字塔。名單順序須為市值由大到小。"""
    order = ["mythical", "legendary", "epic", "rare", "common"]
    out: dict[str, str] = {}
    cursor = 0
    for name in order:
        count = tiers[name]["count"]
        for symbol in ranked_symbols[cursor:cursor + count]:
            out[symbol] = name
        cursor += count
    # 名單長於金字塔設定時，多出來的一律歸為普通。
    for symbol in ranked_symbols[cursor:]:
        out[symbol] = "common"
    return out


def build_move_pool(element: str, volatility_pct: float,
                    templates: dict, threshold: float = 50.0) -> list[dict]:
    """招式池。波動率高者偏魔法（常跳空的妖股），低者偏物理（穩健權值股）。

    判定用的是**名單內百分位**而非絕對波動率：台股整體波動遠高於直覺，
    2026/08 前 50 大的波動率中位數即達 72%，用絕對門檻會把幾乎所有股票
    都判成妖股——包含穩健權值股。相對排名才能真的分出「這批裡誰比較妖」。

    ⚠️ 招式清單尚未定案，此為 v0.1：依偏向取 3 招主系 + 1 招副系。
    """
    magical_bias = volatility_pct >= threshold
    primary = "magical" if magical_bias else "physical"
    secondary = "physical" if magical_bias else "magical"

    pool = [{**m, "category": primary, "element": element}
            for m in templates[primary][:3]]
    pool += [{**m, "category": secondary, "element": element}
             for m in templates[secondary][:1]]
    return sorted(pool, key=lambda m: m["learn_level"])


def build_species(metrics: dict[str, dict], ranked_symbols: list[str],
                  balance: dict) -> tuple[list[dict], list[str]]:
    """產出種族層。metrics 為 {代號: 各項原始數據}，ranked_symbols 依市值排序。

    回傳 (species 清單, 警告訊息)。
    """
    tiers = balance["rarity_tiers"]
    elements = load_json("elements.json")
    moves = load_json("moves.json")
    rarity = assign_rarity(ranked_symbols, tiers)
    warnings: list[str] = []

    # 先把六圍各來源在名單內做百分位排名；複合項取各分量百分位的平均。
    ranks: dict[str, dict[str, float]] = {}
    for stat, components in STAT_SOURCES.items():
        parts: list[dict[str, float]] = []
        for field, reverse in components:
            values = {s: metrics[s].get(field) for s in ranked_symbols}
            missing = [s for s, v in values.items() if v is None]
            if missing:
                warnings.append(
                    f"{stat}（{field}）缺值 {len(missing)} 檔，以中位百分位替代："
                    f"{', '.join(missing[:8])}{'…' if len(missing) > 8 else ''}")
            pct = percentile_ranks(values)
            if reverse:
                pct = {s: 100 - p for s, p in pct.items()}
            parts.append(pct)
        ranks[stat] = {s: sum(p[s] for p in parts) / len(parts)
                       for s in ranked_symbols}

    # 招式偏向另外取一次波動率百分位（未反向），與魔防的反向排名分開。
    vol_pct = percentile_ranks({s: metrics[s].get("volatility_60d")
                                for s in ranked_symbols})

    species: list[dict] = []
    for rank_index, symbol in enumerate(ranked_symbols, start=1):
        m = metrics[symbol]
        tier_name = rarity[symbol]
        tier = tiers[tier_name]
        grades = {stat: grade(ranks[stat][symbol]) for stat in STAT_SOURCES}
        element = m["element"]

        entry = {
            "symbol": symbol,
            "name": m["name"],
            "generation": 1,
            "element": element,
            "rarity": tier_name,
            "rarity_rank": rank_index,
            "bst": tier["bst"],
            "base_stats": allocate(grades, tier["bst"]),
            "growth_group": tier["growth_group"],
            "move_pool": build_move_pool(element, vol_pct[symbol], moves),
            "art": {"placeholder_color": elements["colors"][element], "sprite": None},
        }
        if m.get("industry"):
            entry["industry"] = m["industry"]

        source_metrics = {
            k: v for k, v in {
                "market_cap": m.get("market_cap"),
                "shares_outstanding": m.get("shares_outstanding"),
                "revenue_monthly": m.get("revenue"),
                "revenue_yoy": m.get("revenue_yoy"),
                "eps": m.get("eps"),
                "debt_ratio": m.get("debt_ratio"),
                "turnover_ratio": m.get("turnover_ratio"),
                "volatility_60d": m.get("volatility_60d"),
                "book_value_per_share": m.get("book_value_per_share"),
            }.items() if v is not None
        }
        if source_metrics:
            entry["source_metrics"] = source_metrics
        if m.get("etf_holder_count") is not None:
            entry["etf_scout"] = {"holder_count": m["etf_holder_count"]}

        species.append(entry)

    return species, warnings
