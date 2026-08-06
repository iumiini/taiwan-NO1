"""財報資料層：多業別端點合併、欄位名容錯、季度快取與沿用上一季。

解決三個實測到的問題：

1. **季報公布空窗期**
   TWSE 財報端點是滾動的當季快照，不是全量歷史。2026/08/05 實測（Q2 截止日
   8/14 前）上市一般業僅 82/1093 家已公布、金控業 0 家。空窗期落在每年 5、8、
   11 月初。作法：每季寫入快取並保留上一季，新季未公布即沿用上一季。

2. **快取必須進版控**
   GitHub Actions 每次執行都是全新容器，寫在 runner 本機的快取不會保留。
   因此快取目錄 data/fundamentals/ 是被追蹤的，由排程 commit 回 repo。

3. **欄位名不穩定**
   同一端點 2026/08/05 出表為「資產總額」、08/06 出表變成「資產總計」。
   因此欄位一律經 FIELD_ALIASES 正規化，並保留模糊比對作為最後手段。

用法：
    python3 -m src.fundamentals refresh          # 抓當季、併入快取
    python3 -m src.fundamentals resolve 2330 2317  # 查各檔實際採用的季別
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
BASE = "https://openapi.twse.com.tw/v1/opendata"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "fundamentals"

# 財報依業別拆成多支端點，需全部抓取後合併。
# 務必使用 _L_（上市公司）系列，_X_ 是公發公司（未上市），欄位名亦不同。
INDUSTRY_SUFFIXES = ["ci", "basi", "fh", "ins", "bd", "mim"]
BALANCE_SHEET = [f"t187ap07_L_{s}" for s in INDUSTRY_SUFFIXES]
INCOME_STATEMENT = [f"t187ap06_L_{s}" for s in INDUSTRY_SUFFIXES]

# TWSE 會在不同出表日使用不同欄位名，故逐一列出已知寫法。
FIELD_ALIASES: dict[str, list[str]] = {
    "total_assets": ["資產總計", "資產總額"],
    "total_liabilities": ["負債總計", "負債總額"],
    "total_equity": ["權益總計", "權益總額"],
    "share_capital": ["股本"],
    "book_value_per_share": ["每股參考淨值"],
    "eps": ["基本每股盈餘（元）", "基本每股盈餘(元)"],
}

# 模糊比對的關鍵詞，僅在別名全部落空時啟用。
FUZZY_HINTS: dict[str, tuple[str, ...]] = {
    "total_assets": ("資產總",),
    "total_liabilities": ("負債總",),
    "total_equity": ("權益總",),
    "book_value_per_share": ("每股", "淨值"),
    "eps": ("每股盈餘",),
}


def _get(endpoint: str, timeout: int = 90, attempts: int = 4) -> list[dict]:
    """抓取單一端點。連線重置與逾時會退避重試——實測 TWSE 偶發 ECONNRESET。"""
    url = f"{BASE}/{endpoint}"
    last_error: Exception | None = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                body = json.loads(r.read().decode())
            break
        except (urllib.error.URLError, ConnectionError, json.JSONDecodeError) as e:
            last_error = e
            if i < attempts - 1:
                time.sleep(2 ** i)
    else:
        raise RuntimeError(f"{endpoint} 連續 {attempts} 次失敗：{last_error}")

    # 端點無資料時回傳一筆全空欄位的列，視為空。
    if isinstance(body, list) and body and not body[0].get("公司代號"):
        return []
    return body if isinstance(body, list) else []


def _num(value) -> float | None:
    """TWSE 數值為帶逗號的字串，無資料時為空字串或 '-'。"""
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def pick(record: dict, field: str) -> float | None:
    """依別名取值；全部落空時改用關鍵詞模糊比對。"""
    for alias in FIELD_ALIASES.get(field, []):
        if alias in record:
            return _num(record[alias])
    hints = FUZZY_HINTS.get(field)
    if hints:
        for key in record:
            if all(h in key for h in hints):
                return _num(record[key])
    return None


def period_of(record: dict) -> str | None:
    """從民國年度與季別組出 '115Q2' 形式的期別。"""
    year, quarter = record.get("年度"), record.get("季別")
    return f"{year}Q{quarter}" if year and quarter else None


def fetch_current_quarter() -> tuple[dict[str, dict], dict[str, int]]:
    """抓取所有業別端點並合併。回傳 (依期別分組的公司資料, 各端點筆數)。"""
    merged: dict[str, dict[str, dict]] = {}
    counts: dict[str, int] = {}

    for endpoint in BALANCE_SHEET:
        rows = _get(endpoint)
        counts[endpoint] = len(rows)
        for r in rows:
            period, symbol = period_of(r), r.get("公司代號")
            if not (period and symbol):
                continue
            entry = merged.setdefault(period, {}).setdefault(symbol, {"symbol": symbol})
            entry.update({
                "name": r.get("公司名稱"),
                "total_assets": pick(r, "total_assets"),
                "total_liabilities": pick(r, "total_liabilities"),
                "share_capital": pick(r, "share_capital"),
                "book_value_per_share": pick(r, "book_value_per_share"),
            })

    for endpoint in INCOME_STATEMENT:
        rows = _get(endpoint)
        counts[endpoint] = len(rows)
        for r in rows:
            period, symbol = period_of(r), r.get("公司代號")
            if not (period and symbol):
                continue
            entry = merged.setdefault(period, {}).setdefault(symbol, {"symbol": symbol})
            entry.setdefault("name", r.get("公司名稱"))
            entry["eps"] = pick(r, "eps")

    # 負債比：兩個欄位都齊備才計算，避免填入誤導性的 0。
    for companies in merged.values():
        for e in companies.values():
            a, l = e.get("total_assets"), e.get("total_liabilities")
            e["debt_ratio"] = round(l / a * 100, 2) if a and l is not None and a > 0 else None

    return merged, counts


def load_snapshot(period: str) -> dict:
    path = CACHE_DIR / f"{period}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_snapshot(period: str, companies: dict[str, dict]) -> Path:
    """併入既有快取後寫回。公司陸續公布，同期快取只增不減。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_snapshot(period).get("companies", {})
    existing.update(companies)
    payload = {
        "period": period,
        "updated_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        "company_count": len(existing),
        "companies": dict(sorted(existing.items())),
    }
    path = CACHE_DIR / f"{period}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def cached_periods() -> list[str]:
    """已快取的期別，新到舊排序。"""
    periods = [p.stem for p in CACHE_DIR.glob("*Q[1-4].json")]
    return sorted(periods, key=lambda s: (int(s.split("Q")[0]), int(s.split("Q")[1])),
                  reverse=True)


def resolve(symbols: list[str]) -> tuple[dict[str, dict], list[str]]:
    """為每檔取用最新一期有資料者，缺漏則往前一季找。

    回傳 (資料, 警告訊息)。資料中的 fiscal_period 標明實際採用的季別，
    讓 species.json 能誠實揭露每隻怪的種族值來自哪一季。
    """
    periods = cached_periods()
    warnings: list[str] = []
    if not periods:
        return {}, ["財報快取為空，請先執行 refresh"]

    snapshots = {p: load_snapshot(p).get("companies", {}) for p in periods}
    out: dict[str, dict] = {}
    fallback_count = 0

    for symbol in symbols:
        for period in periods:
            entry = snapshots[period].get(symbol)
            if entry and entry.get("debt_ratio") is not None:
                out[symbol] = {**entry, "fiscal_period": period}
                if period != periods[0]:
                    fallback_count += 1
                break
        else:
            warnings.append(f"{symbol}：所有快取期別皆無財報資料")

    if fallback_count:
        warnings.append(
            f"{fallback_count} 檔沿用較舊季別（最新期 {periods[0]} 尚未公布）")
    return out, warnings


def _cmd_refresh() -> int:
    merged, counts = fetch_current_quarter()
    print("各端點筆數：")
    for endpoint, n in counts.items():
        print(f"  {endpoint:<20} {n:>5}")
    if not merged:
        print("\n⚠️ 所有端點皆無資料，快取未變動")
        return 1
    print()
    for period, companies in sorted(merged.items()):
        path = save_snapshot(period, companies)
        total = json.loads(path.read_text())["company_count"]
        print(f"✅ {period}: 本次取得 {len(companies)} 家，快取累計 {total} 家 → {path}")
    return 0


def _cmd_resolve(symbols: list[str]) -> int:
    data, warnings = resolve(symbols)
    print(f"{'代號':<6}{'名稱':<10}{'採用季別':<10}{'負債比%':>9}{'EPS':>9}")
    print("-" * 46)
    for s in symbols:
        e = data.get(s)
        if not e:
            print(f"{s:<6}{'—':<10}{'查無':<10}")
            continue
        eps = e.get("eps")
        print(f"{s:<6}{(e.get('name') or '')[:8]:<10}{e['fiscal_period']:<10}"
              f"{e['debt_ratio']:>9.2f}{('—' if eps is None else f'{eps:>9.2f}')}")
    for w in warnings:
        print(f"\n⚠️ {w}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("refresh", "resolve"):
        print(__doc__)
        return 2
    if argv[1] == "refresh":
        return _cmd_refresh()
    return _cmd_resolve(argv[2:] or ["2330", "2317", "2603", "2412", "2881"])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
