"""
策略层：从价格序列计算多因子加权分 → 建议金额。

优化点：不再使用“5 个信号各 1 分”的硬触发模型，改为 100 分连续/半连续评分：
  1. 价格分位 35 分：越接近近 1 年低位，分越高
  2. 长期趋势 20 分：MA200 上方更健康，略低于 MA200 仍给部分分
  3. RSI 动量 20 分：偏弱/超跌更适合定投加码，过热降分
  4. 回撤幅度 15 分：从阶段高点回撤越多，低吸价值越高
  5. 布林位置 10 分：越靠近/低于下轨，短期性价比越高

风险熔断：价格显著低于 MA200 且 RSI 过热 → 强制 0x
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
    points: float = 0.0


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
    score_max: int = 100
    details: list[SignalDetail] = field(default_factory=list)
    circuit_breaker: bool = False
    metrics: dict = field(default_factory=dict)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _score_to_action(score: int) -> tuple[float, str]:
    """100 分制得分映射为定投金额系数和标签。"""
    if score >= 80:
        return 2.0, "重点加仓"
    if score >= 65:
        return 1.5, "加码定投"
    if score >= 45:
        return 1.0, "正常定投"
    if score >= 25:
        return 0.5, "减半定投"
    return 0.0, "暂缓"


def _apply_risk_cap(mult: float, label: str, ma_gap: float, pct: float, rsi: float) -> tuple[float, str]:
    """根据趋势破位和过热状态限制最高定投倍数。"""
    cap = 2.0
    reason = ""

    if ma_gap <= -0.20:
        cap, reason = 0.5, "严重破位，限制最高 0.5x"
    elif ma_gap <= -0.10:
        cap, reason = 1.0, "明显破位，限制最高 1.0x"

    if pct >= 0.90 or (pct >= 0.80 and rsi >= C.RSI_OVERBOUGHT):
        cap, reason = min(cap, 0.5), "位置偏高/过热，限制最高 0.5x"

    if mult > cap:
        capped_label = f"{label}（{reason}）"
        return cap, capped_label
    return mult, label


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

    # ---- 100 分加权评分 ----
    details: list[SignalDetail] = []

    # 1. 价格分位：越低越便宜，最高 35 分；80% 以上不给分
    pct = float(last["price_pct"])
    pct_points = round(_clamp((0.80 - pct) / 0.80, 0, 1) * 35, 1)
    pct_ok = pct <= C.PERCENTILE_BUY
    details.append(SignalDetail(
        name="近 1 年价格分位（权重35）",
        triggered=pct_ok,
        value=f"{pct*100:.1f}% / 得分 {pct_points:.1f}",
        threshold=f"越低越好；≤ {int(C.PERCENTILE_BUY*100)}% 视为便宜",
        points=pct_points,
    ))

    # 2. 趋势：MA200 上方满分；低于 MA200 但偏离不大给部分分
    ma_gap = float(last["close"] / last["ma_long"] - 1)
    trend_points = round(_clamp((ma_gap + 0.10) / 0.10, 0, 1) * 20, 1)
    trend_ok = bool(last["close"] > last["ma_long"])
    details.append(SignalDetail(
        name="长期趋势 MA200（权重20）",
        triggered=trend_ok,
        value=f"close={last['close']:.4f} / MA200={last['ma_long']:.4f} / 偏离 {ma_gap*100:.2f}% / 得分 {trend_points:.1f}",
        threshold="MA200 上方满分；低于 10% 后为 0 分",
        points=trend_points,
    ))

    # 3. RSI：超跌/偏弱更适合加码，过热不给分
    rsi = float(last["rsi"])
    rsi_points = round(_clamp((70 - rsi) / 40, 0, 1) * 20, 1)
    rsi_ok = rsi <= C.RSI_OVERSOLD
    details.append(SignalDetail(
        name="RSI(14) 动量温度（权重20）",
        triggered=rsi_ok,
        value=f"{rsi:.1f} / 得分 {rsi_points:.1f}",
        threshold=f"≤ {C.RSI_OVERSOLD} 为超跌；≥ {C.RSI_OVERBOUGHT} 过热",
        points=rsi_points,
    ))

    # 4. 回撤：回撤越大，低吸分越高；15% 及以上满分
    dd = float(last["dd60"])
    dd_points = round(_clamp(dd / 0.15, 0, 1) * 15, 1)
    dd_ok = dd >= C.DRAWDOWN_BUY
    details.append(SignalDetail(
        name="距 60 日高点回撤（权重15）",
        triggered=dd_ok,
        value=f"{dd*100:.2f}% / 得分 {dd_points:.1f}",
        threshold=f"≥ {int(C.DRAWDOWN_BUY*100)}% 开始有低吸意义；15% 满分",
        points=dd_points,
    ))

    # 5. 布林位置：越靠近下轨越高分
    bb_width = float(last["bb_upper"] - last["bb_lower"])
    bb_pos = 0.5 if bb_width <= 0 else float((last["close"] - last["bb_lower"]) / bb_width)
    bb_points = round(_clamp((0.50 - bb_pos) / 0.50, 0, 1) * 10, 1)
    bb_ok = bool(last["close"] <= last["bb_lower"])
    details.append(SignalDetail(
        name="布林带位置（权重10）",
        triggered=bb_ok,
        value=f"位置={bb_pos:.2f} / close={last['close']:.4f} / 下轨={last['bb_lower']:.4f} / 得分 {bb_points:.1f}",
        threshold="≤ 下轨为强低位；中轨以上 0 分",
        points=bb_points,
    ))

    score = int(round(sum(d.points for d in details)))

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
        mult, label = _score_to_action(score)
        mult, label = _apply_risk_cap(mult, label, ma_gap, pct, rsi)

    amount = round(C.BASE_AMOUNT * mult, 2)

    metrics = {
        "MA200": float(last["ma_long"]),
        "RSI14": rsi,
        "1Y分位": pct,
        "60日回撤": dd,
        "MA200偏离": ma_gap,
        "布林位置": bb_pos,
        "布林下轨": float(last["bb_lower"]),
        "布林上轨": float(last["bb_upper"]),
    }

    return Advice(
        target_name=target_name,
        buy_code=buy_code,
        last_date=pd.Timestamp(last["date"]),
        last_close=float(last["close"]),
        score=score,
        score_max=100,
        multiplier=mult,
        amount=amount,
        label=label,
        details=details,
        circuit_breaker=circuit,
        metrics=metrics,
    )
