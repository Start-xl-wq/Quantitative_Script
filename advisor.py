"""
策略层：从价格序列计算 5 个子信号 → 总分 → 建议金额。

5 个信号（每个 1 分，满分 5）：
  1. 趋势：收盘 > MA200
  2. 价格分位：近 1 年百分位 ≤ 40%
  3. RSI(14) ≤ 35（超跌）
  4. 距 60 日高点回撤 ≥ 5%
  5. 收盘 ≤ 布林下轨（20, 2σ）

风险熔断：价格 < MA200 且 RSI ≥ 70  → 强制 0x
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pandas_ta as ta  # noqa: F401  注册 .ta accessor

import config as C


@dataclass
class SignalDetail:
    name: str
    triggered: bool
    value: str         # 当前实际值（展示用）
    threshold: str     # 触发阈值（展示用）


@dataclass
class Advice:
    target_name: str
    buy_code: str
    last_date: pd.Timestamp
    last_close: float
    score: int
    multiplier: float
    amount: float
    label: str
    details: list[SignalDetail] = field(default_factory=list)
    circuit_breaker: bool = False
    metrics: dict = field(default_factory=dict)


def evaluate(df: pd.DataFrame, target_name: str, buy_code: str) -> Advice:
    """评估单一标的，返回 Advice 对象。"""
    if len(df) < max(C.MA_LONG, C.PERCENTILE_WINDOW) + 5:
        raise ValueError(
            f"{target_name} 历史数据不足（{len(df)} 行），至少需要 "
            f"{max(C.MA_LONG, C.PERCENTILE_WINDOW) + 5} 行"
        )

    df = df.copy()
    close = df["close"]

    # ---- 计算指标 ----
    df["ma_long"] = df.ta.sma(length=C.MA_LONG)
    df["rsi"] = df.ta.rsi(length=C.RSI_PERIOD)
    bbands = df.ta.bbands(length=C.BBANDS_PERIOD, std=C.BBANDS_STD)
    bb_lower_col = next(c for c in bbands.columns if c.startswith("BBL_"))
    bb_upper_col = next(c for c in bbands.columns if c.startswith("BBU_"))
    df["bb_lower"] = bbands[bb_lower_col]
    df["bb_upper"] = bbands[bb_upper_col]

    # 1 年价格分位（用截止当日的滚动窗口算分位）
    df["price_pct"] = (
        close.rolling(C.PERCENTILE_WINDOW)
        .apply(lambda s: (s.rank(pct=True).iloc[-1]), raw=False)
    )

    # 60 日高点回撤
    df["high60"] = close.rolling(C.DRAWDOWN_WINDOW).max()
    df["dd60"] = 1 - close / df["high60"]

    last = df.iloc[-1]

    # ---- 5 个信号 ----
    details: list[SignalDetail] = []

    # 1. 趋势
    trend_ok = bool(last["close"] > last["ma_long"])
    details.append(SignalDetail(
        name="趋势（价 > MA200）",
        triggered=trend_ok,
        value=f"close={last['close']:.4f} / MA200={last['ma_long']:.4f}",
        threshold="close > MA200",
    ))

    # 2. 价格分位 ≤ 40%
    pct = float(last["price_pct"])
    pct_ok = pct <= C.PERCENTILE_BUY
    details.append(SignalDetail(
        name=f"近 1 年价格分位 ≤ {int(C.PERCENTILE_BUY*100)}%",
        triggered=pct_ok,
        value=f"{pct*100:.1f}%",
        threshold=f"≤ {int(C.PERCENTILE_BUY*100)}%",
    ))

    # 3. RSI ≤ 35
    rsi = float(last["rsi"])
    rsi_ok = rsi <= C.RSI_OVERSOLD
    details.append(SignalDetail(
        name=f"RSI(14) ≤ {C.RSI_OVERSOLD}",
        triggered=rsi_ok,
        value=f"{rsi:.1f}",
        threshold=f"≤ {C.RSI_OVERSOLD}",
    ))

    # 4. 距 60 日高点回撤 ≥ 5%
    dd = float(last["dd60"])
    dd_ok = dd >= C.DRAWDOWN_BUY
    details.append(SignalDetail(
        name=f"距 60 日高点回撤 ≥ {int(C.DRAWDOWN_BUY*100)}%",
        triggered=dd_ok,
        value=f"{dd*100:.2f}%",
        threshold=f"≥ {int(C.DRAWDOWN_BUY*100)}%",
    ))

    # 5. 触及布林下轨
    bb_ok = bool(last["close"] <= last["bb_lower"])
    details.append(SignalDetail(
        name="触及布林下轨（20, 2σ）",
        triggered=bb_ok,
        value=f"close={last['close']:.4f} / 下轨={last['bb_lower']:.4f}",
        threshold="close ≤ 下轨",
    ))

    score = sum(1 for d in details if d.triggered)

    # ---- 熔断 ----
    circuit = (
        C.ENABLE_CIRCUIT_BREAKER
        and (not trend_ok)
        and rsi >= C.RSI_OVERBOUGHT
    )

    if circuit:
        mult = 0.0
        label = "熔断暂缓（趋势空头 + 短期超买）"
    else:
        mult = C.SCORE_TO_MULT[score]
        label = C.SCORE_LABEL[score]

    amount = round(C.BASE_AMOUNT * mult, 2)

    metrics = {
        "MA200": float(last["ma_long"]),
        "RSI14": rsi,
        "1Y分位": pct,
        "60日回撤": dd,
        "布林下轨": float(last["bb_lower"]),
        "布林上轨": float(last["bb_upper"]),
    }

    return Advice(
        target_name=target_name,
        buy_code=buy_code,
        last_date=pd.Timestamp(last["date"]),
        last_close=float(last["close"]),
        score=score,
        multiplier=mult,
        amount=amount,
        label=label,
        details=details,
        circuit_breaker=circuit,
        metrics=metrics,
    )
