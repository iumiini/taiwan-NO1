"""外部資料源封裝：富果行情 + TWSE 公開資料。

所有請求統一走 `_request`，具備退避重試——實測 TWSE 偶發 ECONNRESET、
富果歷史 K 線有「未滿一年」的單次跨度上限。

金鑰只有歷史 K 線、大盤指數、個股 ticker 需要；每日全市場行情（snapshot）
與所有 TWSE 端點都不需要。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

FUGLE = "https://api.fugle.tw/marketdata/v1.0"
TWSE = "https://openapi.twse.com.tw/v1"

# 富果歷史 K 線單次跨度須「未滿一年」，故以此天數分段。
CANDLE_CHUNK_DAYS = 330


class SourceError(RuntimeError):
    pass


def _request(url: str, headers: dict | None = None, timeout: int = 120,
             attempts: int = 5):
    """統一請求入口。

    429 視為可重試並採較長的退避——富果免費額度在連續抓取歷史 K 線時會觸發。
    其餘 4xx 是請求本身的問題，重試無益，直接拋出。
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = e.headers.get("Retry-After")
                delay = int(retry_after) if (retry_after or "").isdigit() else 5 * (i + 1)
                last = e
                if i < attempts - 1:
                    time.sleep(delay)
                    continue
            elif 400 <= e.code < 500:
                raise SourceError(f"{url} → {e.code} {e.read().decode()[:200]}") from e
            last = e
        except (urllib.error.URLError, ConnectionError, json.JSONDecodeError) as e:
            last = e
        if i < attempts - 1:
            time.sleep(2 ** i)
    raise SourceError(f"{url} 連續 {attempts} 次失敗：{last}")


# --------------------------------------------------------------------------
# 富果
# --------------------------------------------------------------------------

# 富果免費額度在連續抓取時會觸發 429，主動節流比事後重試便宜。
FUGLE_MIN_INTERVAL = float(os.environ.get("FUGLE_MIN_INTERVAL", "1.1"))
_last_fugle_call = 0.0


def _throttle() -> None:
    global _last_fugle_call
    wait = FUGLE_MIN_INTERVAL - (time.monotonic() - _last_fugle_call)
    if wait > 0:
        time.sleep(wait)
    _last_fugle_call = time.monotonic()


def _fugle_get(path: str, auth: bool = True):
    """所有富果請求的唯一入口，確保每一次呼叫都經過節流。"""
    headers = {}
    if auth:
        key = os.environ.get("FUGLE_API_KEY")
        if not key:
            raise SourceError("需要環境變數 FUGLE_API_KEY")
        headers["X-API-KEY"] = key
    _throttle()
    return _request(f"{FUGLE}/{path}", headers=headers)


def snapshot(market: str = "TSE") -> dict:
    """全市場當日行情。**免金鑰**，是每日結算的主食。

    注意：此端點的 tradeVolume 單位為「張」，與歷史 K 線的「股」不同，
    兩者不可混用。本管線只用它取收盤價做市值排名，量能一律取自 K 線。
    """
    return _fugle_get(f"snapshot/quotes/{market}", auth=False)


def candles(symbol: str, days: int = 420) -> list[dict]:
    """歷史日 K，新到舊排序。分段抓以避開「未滿一年」上限。"""
    end, start = date.today(), date.today() - timedelta(days=days)
    out: dict[str, dict] = {}
    cursor = end
    while cursor > start:
        seg_start = max(start, cursor - timedelta(days=CANDLE_CHUNK_DAYS))
        body = _fugle_get(
            f"stock/historical/candles/{symbol}?from={seg_start}&to={cursor}")
        for row in body.get("data", []):
            out[row["date"]] = row
        cursor = seg_start - timedelta(days=1)
    return [out[d] for d in sorted(out, reverse=True)]


def ticker(symbol: str) -> dict:
    """個股基本狀態。含 isAttention（注意股）與 isDisposition（處置股），
    超進化與禁閉室系統的資料源——不需另爬公告。"""
    return _fugle_get(f"stock/intraday/ticker/{symbol}")


def market_index() -> dict:
    """發行量加權股價指數，世界天氣的來源。"""
    return _fugle_get("stock/intraday/quote/IX0001")


# --------------------------------------------------------------------------
# TWSE（全部免金鑰）
# --------------------------------------------------------------------------

def _twse(path: str, timeout: int = 120) -> list[dict]:
    body = _request(f"{TWSE}/{path}", timeout=timeout)
    return body if isinstance(body, list) else []


def valuations() -> dict[str, dict]:
    """本益比、殖利率、股價淨值比。每日全市場，是空窗期回推 EPS 的依據。"""
    return {r["Code"]: r for r in _twse("exchangeReport/BWIBBU_ALL")}


def company_profiles() -> dict[str, dict]:
    """公司基本資料。提供已發行普通股數——市值＝股數 × 收盤價。"""
    return {r["公司代號"]: r for r in _twse("opendata/t187ap03_L")}


def monthly_revenue() -> dict[str, dict]:
    """月營收與去年同月增減（%）。YoY 由官方直接提供，不需自算。"""
    return {r["公司代號"]: r for r in _twse("opendata/t187ap05_L")}


def turnover_ratios() -> dict[str, dict]:
    """月成交統計，含官方直接給的週轉率。

    此端點約 6.6 MB、需時約 80 秒，且含多月份歷史——timeout 設太短會拿到
    截斷的 JSON 而非錯誤碼。此處只保留每檔最新月份。
    """
    latest: dict[str, dict] = {}
    for r in _twse("exchangeReport/FMSRFK_ALL", timeout=180):
        code, month = r.get("Code"), r.get("Month")
        if not code:
            continue
        if code not in latest or month > latest[code]["Month"]:
            latest[code] = r
    return latest
