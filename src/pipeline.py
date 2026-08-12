"""每日管線：抓資料 → 怪獸化 → 產出圖鑑 JSON。

流程對應設計文件第六章的零成本架構：

    收盤後排程 → 拉富果 + TWSE → StatMapper 在雲端執行 → 輸出靜態 JSON

輸出至 site/data/，由 GitHub Pages 託管；Unity 遊戲與圖鑑網頁讀同一包資料。
data/ 則是輸入端（平衡參數、屬性對照、財報快取），兩者刻意分離。

用法：
    FUGLE_API_KEY=xxx python3 -m src.pipeline --size 50
    FUGLE_API_KEY=xxx python3 -m src.pipeline --size 10 --skip-ticker
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import fundamentals, indicators, manifest, sources, statmapper, universe

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "site" / "data"
SCHEMA_VERSION = "0.1.0"
SETTLEMENT_HOUR = 19

# 大盤漲跌超過此幅度即切換世界天氣。
WORLD_WEATHER_THRESHOLD = 1.5


@contextlib.contextmanager
def single_instance():
    """避免兩份管線同時執行——實測同時跑會互相覆寫 site/data 的產出。

    鎖檔記錄 PID；若前一次執行被強制中斷留下死鎖，會自動接管。
    """
    lock = ROOT / ".pipeline.lock"
    if lock.exists():
        pid = lock.read_text().strip()
        alive = pid.isdigit() and Path(f"/proc/{pid}").exists()
        if alive:
            raise SystemExit(f"另一份管線正在執行（PID {pid}）。等它結束，或確認後刪除 {lock}")
        print(f"發現殘留鎖檔（PID {pid} 已不存在），接管")
    lock.write_text(str(os.getpid()))
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _f(value) -> float | None:
    """TWSE 數值為字串，缺值為空字串或 '-'。"""
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def season_of(d: datetime) -> str:
    return f"{d.year}-S{(d.month - 1) // 3 + 1}"


def to_ad_period(roc_period: str) -> str:
    """民國期別轉西元，例如 115Q2 → 2026Q2。

    TWSE 一律使用民國年，快取沿用來源格式；對外發布的 JSON 則統一西元，
    與 market_date 等 ISO 日期欄位一致。
    """
    year, quarter = roc_period.split("Q")
    return f"{int(year) + 1911}Q{quarter}"


# --------------------------------------------------------------------------
# 每日層各欄位
# --------------------------------------------------------------------------

def pe_tag(pe: float | None, tags: dict) -> str:
    """身價標籤。EPS 為負時 TWSE 的本益比為 '-'，即神秘系。"""
    if pe is None:
        return "mysterious"
    if pe < tags["undervalued_below"]:
        return "undervalued"
    if pe > tags["popular_above"]:
        return "popular"
    return "fair"


def net_value_shield(pb: float | None, base_hp: int, cfg: dict) -> dict:
    """淨值盾。門檻制——少數派才有盾，好奇心即教育入口。"""
    if pb is None or pb >= cfg["pb_threshold"]:
        return {"tier": "none", "amount": 0}
    tier = "gold" if pb < cfg["gold_threshold"] else "silver"
    depth = (cfg["pb_threshold"] - pb) / cfg["pb_threshold"]
    amount = round(cfg["amount_formula_k"] * depth * base_hp)
    return {"tier": tier, "amount": max(1, amount)}


def multipliers(weather: list[str], has_kd_shield: bool, balance: dict) -> dict:
    """當日乘區。平衡調整在雲端算好後直接下發，玩家不用更新 App。"""
    out = {"crit_rate": balance["battle"]["base_crit_rate"], "damage_taken": 1.0}
    for state in weather:
        for key, value in balance["weather_effects"].get(state, {}).items():
            if key == "crit_rate":
                out["crit_rate"] = value
            elif key == "damage_taken":
                out["damage_taken"] *= value
            else:
                out[key] = round(out.get(key, 1.0) * value, 4)
    if has_kd_shield:
        out["damage_taken"] = round(
            out["damage_taken"] * balance["kd_shield"]["damage_taken"], 4)
    return out


def world_state(index: dict, balance: dict) -> dict:
    change = index.get("changePercent") or 0.0
    weather = ("sunny" if change >= WORLD_WEATHER_THRESHOLD
               else "storm" if change <= -WORLD_WEATHER_THRESHOLD else "normal")
    effects = balance.get("world_weather_effects", {}).get(weather, {})
    return {
        "index": {
            "symbol": index.get("symbol", "IX0001"),
            "name": index.get("name"),
            "close": index.get("closePrice"),
            "change_percent": change,
        },
        "weather": weather,
        "business_light": None,
        **({"multipliers": effects} if effects else {}),
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def collect(size: int, skip_ticker: bool, refreeze: bool = False,
            freeze_on: str | None = None) -> tuple[dict, list[str], list[dict]]:
    """抓取所有資料源並彙整成 {代號: 指標}。回傳 (metrics, 名單, 資料源紀錄)。"""
    log = print
    provenance: list[dict] = []

    def note(name: str, endpoint: str, count: int) -> None:
        provenance.append({"name": name, "endpoint": endpoint, "record_count": count})

    log("① 全市場行情快照（免金鑰）…")
    snap = sources.snapshot("TSE")
    quotes = {r["symbol"]: r for r in snap.get("data", [])}
    note("fugle-snapshot-tse", "/snapshot/quotes/TSE", len(quotes))
    log(f"   {len(quotes)} 檔，市場日期 {snap.get('date')}")

    log("② 公司基本資料（股本）…")
    profiles = sources.company_profiles()
    note("twse-company-profile", "/opendata/t187ap03_L", len(profiles))

    caps = universe.market_caps(quotes, profiles)
    season = season_of(datetime.now(TZ))
    market_date = snap.get("date")

    # 名單可指定以過去某一天的收盤價凍結；當日行情仍取最新，兩者用途不同。
    freeze_caps, freeze_date = caps, market_date
    needs_freeze = refreeze or not universe.frozen_path(season).exists()
    if freeze_on and needs_freeze:
        log(f"   以 {freeze_on} 收盤價重算市值供凍結…")
        freeze_caps = universe.caps_from_closes(
            sources.closing_prices_on(freeze_on), profiles, tradable=set(quotes))
        freeze_date = freeze_on

    symbols, created = universe.resolve_season(
        season, freeze_caps, size, freeze_date, refreeze=refreeze)
    log(f"③ 賽季 {season} 名單 {len(symbols)} 檔"
        f"（{f'以 {freeze_date} 收盤凍結' if created else '沿用已凍結名單'}）："
        f"{'、'.join(symbols[:6])}…")

    log("④ 估值（本益比／殖利率／淨值比）…")
    vals = sources.valuations()
    note("twse-bwibbu", "/exchangeReport/BWIBBU_ALL", len(vals))

    log("⑤ 月營收與 YoY…")
    revenue = sources.monthly_revenue()
    note("twse-monthly-revenue", "/opendata/t187ap05_L", len(revenue))

    log("⑥ 週轉率（約 6.6 MB，需時較久）…")
    turnover = sources.turnover_ratios()
    note("twse-turnover", "/exchangeReport/FMSRFK_ALL", len(turnover))

    log("⑦ 財報（快取，缺當季則沿用上一季）…")
    fund, fund_warnings = fundamentals.resolve(symbols)
    note("twse-fundamentals-cache", "data/fundamentals", len(fund))

    elements = universe.load_elements()
    metrics: dict[str, dict] = {}

    log(f"⑧ 歷史 K 線與指標（{len(symbols)} 檔，分段抓取）…")
    missing_quote: list[str] = []
    for i, symbol in enumerate(symbols, 1):
        if symbol not in quotes:
            # 凍結名單的成員可能因下市、暫停交易或轉板而從當日行情消失。
            missing_quote.append(symbol)
            log(f"   ⚠️ {symbol} 不在當日行情中，跳過")
            continue
        rows = sources.candles(symbol)
        if len(rows) < 2:
            log(f"   ⚠️ {symbol} K 線不足，跳過")
            continue
        change_pct = round((rows[0]["close"] / rows[1]["close"] - 1) * 100, 2)
        ind = indicators.compute(rows, change_pct)

        profile = profiles.get(symbol, {})
        rev = revenue.get(symbol, {})
        val = vals.get(symbol, {})
        f = fund.get(symbol, {})
        close = rows[0]["close"]
        pe = _f(val.get("PEratio"))
        pb = _f(val.get("PBratio"))

        # 空窗期回推：季報未公布時，EPS 與每股淨值改由每日估值端點反推。
        eps = f.get("eps")
        eps_source = "financial_statement"
        if eps is None and pe and pe > 0:
            eps, eps_source = round(close / pe, 2), "derived_from_pe"

        metrics[symbol] = {
            "name": (quotes[symbol].get("name") or profile.get("公司簡稱") or symbol),
            "industry": rev.get("產業別"),
            "element": universe.element_of(symbol, rev.get("產業別"), elements),
            "market_cap": round(caps[symbol]),
            "shares_outstanding": _f(profile.get("已發行普通股數或TDR原股發行股數")),
            # 月營收單位為仟元，統一換算為元。
            "revenue": (r * 1000 if (r := _f(rev.get("營業收入-當月營收"))) else None),
            "revenue_yoy": (round(y, 2)
                            if (y := _f(rev.get("營業收入-去年同月增減(%)"))) is not None
                            else None),
            "eps": eps,
            "eps_source": eps_source,
            "debt_ratio": f.get("debt_ratio"),
            "book_value_per_share": (f.get("book_value_per_share")
                                     or (round(close / pb, 2) if pb else None)),
            "fiscal_period": f.get("fiscal_period"),
            "turnover_ratio": _f(turnover.get(symbol, {}).get("TurnoverRatio")),
            "volatility_60d": ind["volatility_60d"],
            "pe": pe, "pb": pb,
            "dividend_yield": _f(val.get("DividendYield")),
            "close": close,
            "change_percent": change_pct,
            "candle": rows[0],
            "indicators": ind,
            "special_state": None,
        }
        if i % 10 == 0 or i == len(symbols):
            log(f"   {i}/{len(symbols)}")

    symbols = [s for s in symbols if s in metrics]

    if not skip_ticker:
        log("⑨ 注意股／處置股狀態（逐檔查詢）…")
        for symbol in symbols:
            t = sources.ticker(symbol)
            metrics[symbol]["special_state"] = (
                "detention" if t.get("isDisposition")
                else "hyper_evolution" if t.get("isAttention") else None)
        note("fugle-ticker", "/stock/intraday/ticker", len(symbols))

    if missing_quote:
        fund_warnings.append(
            f"{len(missing_quote)} 檔不在當日行情中而略過：{'、'.join(missing_quote)}")

    return {"metrics": metrics, "symbols": symbols, "quotes": quotes,
            "market_date": snap.get("date"),
            "fund_warnings": fund_warnings}, symbols, provenance


def build_daily(bundle: dict, species_by_symbol: dict, balance: dict) -> dict:
    metrics, symbols = bundle["metrics"], bundle["symbols"]
    cap_cfg = balance["capture"]
    iv_ranges = balance["iv"]["ranges"]

    # 遭遇權重：同稀有度階層內依週轉率分配，避免與稀有度基礎率打架。
    by_tier: dict[str, list[float]] = {}
    for s in symbols:
        tier = species_by_symbol[s]["rarity"]
        by_tier.setdefault(tier, []).append(metrics[s].get("turnover_ratio") or 0.0)
    tier_median = {t: (statistics.median(v) or 1.0) for t, v in by_tier.items()}

    monsters = []
    for symbol in symbols:
        m = metrics[symbol]
        sp = species_by_symbol[symbol]
        ind = m["indicators"]
        candle = m["candle"]
        quadrant = ind["aura"]["quadrant"]
        state = cap_cfg["aura_state_map"][quadrant]
        shield = ind["kd"]["shield"] if ind["kd"] else False
        median = tier_median[sp["rarity"]] or 1.0
        turnover = m.get("turnover_ratio") or 0.0

        entry = {
            "symbol": symbol,
            "price": {
                "open": candle["open"], "high": candle["high"],
                "low": candle["low"], "close": candle["close"],
                "change_percent": m["change_percent"],
                "volume": candle["volume"],
            },
            "moving_averages": ind["moving_averages"],
            "weather": ind["weather"],
            "aura": ind["aura"],
            "net_value_shield": net_value_shield(
                m["pb"], sp["base_stats"]["hp"], balance["net_value_shield"]),
            "valuation": {
                "pe": m["pe"], "pb": m["pb"],
                "dividend_yield": m["dividend_yield"],
                "pe_tag": pe_tag(m["pe"], balance["valuation_tags"]),
                "market_cap": m["market_cap"],
            },
            "capture": {
                "cost": round(m["close"] * cap_cfg["price_multiplier"], 2),
                "rate_modifier": cap_cfg["state_modifiers"][state],
                "iv_range": dict(iv_ranges[state]),
            },
            "encounter_weight": round(min(3.0, turnover / median) if median else 1.0, 2),
            "special_state": m["special_state"],
            "multipliers": multipliers(ind["weather"], shield, balance),
        }
        if ind["kd"]:
            entry["kd"] = ind["kd"]
        monsters.append(entry)

    now = datetime.now(TZ).replace(microsecond=0)
    settled = now.replace(hour=SETTLEMENT_HOUR, minute=0, second=0)
    return {
        "schema_version": SCHEMA_VERSION,
        "market_date": bundle["market_date"],
        "settled_at": settled.isoformat(),
        "generation": 1,
        "season": season_of(now),
        "world": world_state(sources.market_index(), balance),
        "monsters": monsters,
    }


def run(size: int, skip_ticker: bool, refreeze: bool = False,
        freeze_on: str | None = None) -> int:
    balance = json.loads((DATA_DIR / "balance.json").read_text())
    bundle, symbols, provenance = collect(size, skip_ticker, refreeze, freeze_on)
    if not symbols:
        print("✗ 沒有任何可用資料")
        return 1

    print("⑩ StatMapper 怪獸化…")
    species, warnings = statmapper.build_species(
        bundle["metrics"], symbols, balance)
    species_by_symbol = {s["symbol"]: s for s in species}

    daily = build_daily(bundle, species_by_symbol, balance)
    now = datetime.now(TZ).replace(microsecond=0)
    periods = {bundle["metrics"][s].get("fiscal_period") for s in symbols}
    periods.discard(None)

    species_doc = {
        "schema_version": SCHEMA_VERSION,
        "generation": 1,
        "season": season_of(now),
        "stats_as_of": daily["market_date"],
        **({"fiscal_period": to_ad_period(sorted(periods)[-1])} if periods else {}),
        "species": species,
    }

    derived = [s for s in symbols
               if bundle["metrics"][s]["eps_source"] == "derived_from_pe"]
    if derived:
        warnings.append(
            f"{len(derived)} 檔的 EPS 由收盤價÷本益比回推（季報未公布）")
    warnings += bundle["fund_warnings"]

    print("⑪ 寫出檔案…")
    (OUT_DIR / "daily").mkdir(parents=True, exist_ok=True)
    write = lambda p, o: p.write_text(json.dumps(o, ensure_ascii=False, indent=2) + "\n")
    write(OUT_DIR / "species.json", species_doc)
    write(OUT_DIR / f"daily/{daily['market_date']}.json", daily)
    write(OUT_DIR / "balance.json", balance)

    manifest.build_index(OUT_DIR, daily["market_date"], species_doc,
                         provenance, warnings)

    print(f"\n✅ 完成：{len(species)} 隻怪獸 → {OUT_DIR}")
    for w in warnings:
        print(f"  ⚠️ {w}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="台股寶可夢圖鑑每日管線")
    ap.add_argument("--size", type=int, default=50, help="名單檔數（預設 50）")
    ap.add_argument("--skip-ticker", action="store_true",
                    help="略過注意股／處置股查詢（逐檔請求，較慢）")
    ap.add_argument("--refreeze", action="store_true",
                    help="重新凍結賽季名單（換季或改用其他日期時使用）")
    ap.add_argument("--freeze-on", metavar="YYYY-MM-DD",
                    help="以指定日期的收盤市值凍結名單，預設為執行當日")
    args = ap.parse_args()
    with single_instance():
        return run(args.size, args.skip_ticker, args.refreeze, args.freeze_on)


if __name__ == "__main__":
    sys.exit(main())
