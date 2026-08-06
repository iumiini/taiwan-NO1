"""技術指標計算。全部由歷史日 K 衍生，不依賴任何額外端點。

設計文件的對應關係：
- 均線 → 均線天氣（管攻，只賞不罰）
- KD   → 黃金交叉護盾（管守）
- 量能比 → 氣場四象限（管遭遇與收服鑑定）
- 波動率 → 魔防（反向映射）

輸入的 K 線一律為「新到舊」排序。
"""

from __future__ import annotations

TRADING_DAYS_PER_YEAR = 252


def sma(values: list[float], n: int) -> float | None:
    return sum(values[:n]) / n if len(values) >= n else None


def moving_averages(closes: list[float]) -> dict[str, float | None]:
    return {f"ma{n}": (round(v, 2) if (v := sma(closes, n)) is not None else None)
            for n in (5, 20, 60, 240)}


def weather(close: float, ma: dict[str, float | None]) -> list[str]:
    """均線天氣。只賞不罰：跌破短中期均線只是回常態，唯有跌破年線小減益。"""
    states = []
    if ma["ma5"] is not None and close > ma["ma5"]:
        states.append("excited")
    if ma["ma20"] is not None and close > ma["ma20"]:
        states.append("tailwind")
    if ma["ma240"] is not None and close < ma["ma240"]:
        states.append("depressed")
    return states


def kd(rows: list[dict], period: int = 9) -> tuple[float, float] | None:
    """KD（9,3,3）。由舊到新遞推，初始值 50。"""
    if len(rows) < period:
        return None
    k = d = 50.0
    for i in range(period - 1, len(rows)):
        window = rows[::-1][i - period + 1:i + 1]
        high = max(r["high"] for r in window)
        low = min(r["low"] for r in window)
        close = window[-1]["close"]
        rsv = 50.0 if high == low else (close - low) / (high - low) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
    return round(k, 2), round(d, 2)


def volatility(closes: list[float], n: int = 60) -> float | None:
    """年化波動率（%）。日報酬標準差 × √252。"""
    if len(closes) < n + 1:
        return None
    rets = [closes[i] / closes[i + 1] - 1 for i in range(n)]
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return round((var ** 0.5) * (TRADING_DAYS_PER_YEAR ** 0.5) * 100, 2)


def volume_ratio(volumes: list[int], n: int = 20) -> float | None:
    """當日量 ÷ 近 n 日均量。決定氣場的厚薄與穩定度。"""
    if len(volumes) < n:
        return None
    avg = sum(volumes[:n]) / n
    return round(volumes[0] / avg, 2) if avg > 0 else None


def aura_quadrant(change_percent: float, vol_ratio: float | None) -> str:
    """氣場四象限。價決定發光顏色，量決定氣場厚薄。

    量縮價漲＝幻影暴走（騙局個體，烙印加成打折）；
    量縮價跌＝蓄力假寐（買壓枯竭可能落底，識貨者獲勵）。
    """
    if vol_ratio is None:
        return "true_surge" if change_percent >= 0 else "true_weak"
    heavy = vol_ratio >= 1.0
    if change_percent >= 0:
        return "true_surge" if heavy else "phantom_surge"
    return "true_weak" if heavy else "dormant"


def compute(rows: list[dict], change_percent: float) -> dict:
    """由 K 線一次算出每日層所需的全部指標。"""
    closes = [r["close"] for r in rows]
    volumes = [r["volume"] for r in rows]
    ma = moving_averages(closes)
    kd_pair = kd(rows)
    vr = volume_ratio(volumes)
    return {
        "moving_averages": ma,
        "weather": weather(closes[0], ma),
        "kd": ({"k": kd_pair[0], "d": kd_pair[1], "cross": None,
                "shield": kd_pair[0] > kd_pair[1]} if kd_pair else None),
        "aura": {"quadrant": aura_quadrant(change_percent, vr),
                 "volume_ratio": vr if vr is not None else 0.0,
                 "awakening_omen": False},
        "volatility_60d": volatility(closes),
        "candle_count": len(rows),
    }
