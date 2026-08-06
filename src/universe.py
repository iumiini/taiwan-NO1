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


def load_elements() -> dict:
    return json.loads((DATA_DIR / "elements.json").read_text())


def element_of(symbol: str, industry: str | None, table: dict) -> str:
    """屬性系。逐檔覆寫優先於產業別對照——產業別分不出電信與網通等個案。"""
    if symbol in table["overrides"]:
        return table["overrides"][symbol]
    return table["by_industry"].get(industry or "", table["fallback"])
