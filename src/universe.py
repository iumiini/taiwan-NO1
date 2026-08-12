"""選股名單（universe）。

設計文件第十一章定案的候選池為「N 檔主動型 ETF 持股聯集」，但各投信官網
格式不一、需逐家寫擷取器，是目前最大的工程風險。因此此處把名單抽象成
provider：先以市值排名跑通整條管線，ETF 聯集之後作為第二個 provider
插入即可，管線本身不需改動。

稀有度依設計為「世代內」市值排名，故排名一律在選出的名單內重排，
而非沿用全市場名次。
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def market_caps(quotes: dict[str, dict], profiles: dict[str, dict]) -> dict[str, float]:
    """市值＝已發行普通股數 × 收盤價。兩個來源都免金鑰。"""
    caps: dict[str, float] = {}
    for symbol, profile in profiles.items():
        quote = quotes.get(symbol)
        if not quote:
            continue
        try:
            shares = float(profile["已發行普通股數或TDR原股發行股數"])
        except (KeyError, TypeError, ValueError):
            continue
        close = quote.get("closePrice")
        if shares > 0 and close:
            caps[symbol] = shares * close
    return caps


def top_market_cap(caps: dict[str, float], size: int) -> list[str]:
    """市值前 N 名。第一版 provider——名單現成、可立即跑通全鏈路。"""
    return [s for s, _ in sorted(caps.items(), key=lambda kv: -kv[1])[:size]]


PROVIDERS = {"top_market_cap": top_market_cap}

UNIVERSE_DIR = DATA_DIR / "universe"


def frozen_path(season: str) -> Path:
    return UNIVERSE_DIR / f"{season}.json"


def resolve_season(season: str, caps: dict[str, float], size: int,
                   market_date: str, provider: str = "top_market_cap",
                   refreeze: bool = False) -> tuple[list[str], bool]:
    """取得本賽季的名單。已凍結就沿用，否則以當次的市場資料凍結存檔。

    名單一旦固定就不再隨每日市值波動變動——設計文件第五章的「名單固定」，
    避免玩家的怪獸今天在圖鑑、明天消失。實測 2026/08/06 與 08/12 相隔六天，
    純市值排名就已經換掉一檔（合庫金掉出、瑞昱進榜）。

    **凍結日由執行日決定**：想以某一天的收盤名單開賽季，就在那天執行管線
    （或帶 refreeze 重跑）。檔案記錄的是 `market_date`（市場資料日）而非
    系統日期，兩者在收盤後跨日執行時會不同。

    稀有度是世代內市值排名，因此排名順序也一併凍結，不隨股價每日重排。
    回傳 (名單, 是否為本次凍結)。
    """
    path = frozen_path(season)
    if path.exists() and not refreeze:
        return json.loads(path.read_text())["symbols"], False

    symbols = PROVIDERS[provider](caps, size)
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "season": season,
        "provider": provider,
        "size": len(symbols),
        "market_date": market_date,
        "note": "名單與排名以 market_date 當日收盤市值凍結，整季不變；"
                "稀有度依此排名切分金字塔。要改用別的日期，於該日執行管線並帶 --refreeze。",
        "symbols": symbols,
    }, ensure_ascii=False, indent=2) + "\n")
    return symbols, True


def load_elements() -> dict:
    return json.loads((DATA_DIR / "elements.json").read_text())


def element_of(symbol: str, industry: str | None, table: dict) -> str:
    """屬性系。逐檔覆寫優先於產業別對照——產業別分不出電信與網通等個案。"""
    if symbol in table["overrides"]:
        return table["overrides"][symbol]
    return table["by_industry"].get(industry or "", table["fallback"])
