"""
数据获取层：根据代码自动路由到合适的接口。

支持的代码形式：
  - 'sh######' / 'sz######'     → 指数（新浪），原样传
  - '15xxxx' / '16xxxx' / '5xxxxx' → 场内 ETF/LOF（新浪带前缀），失败 fallback efinance
  - '0xxxxx' / '3xxxxx' / '6xxxxx' → A 股票（akshare）
  - 其他 6 位数字              → 场外基金（天天基金单位净值）

也支持显式覆盖：在 TARGETS 中加 "source": "index" / "etf" / "stock" / "fund" / "auto"。
"""

from __future__ import annotations

import re

import pandas as pd

# ---------- 标准化输出列 ----------
_OHLCV = ["date", "open", "high", "low", "close", "volume"]


def fetch_price(code_or_source) -> pd.DataFrame:
    """统一入口。code_or_source 兼容两种调用方式：
    - 字符串 code，如 '159307' / 'sh000510' / '022430' → 自动路由
    - 旧版 (source_type, code) 元组 → 兼容老 config
    """
    if isinstance(code_or_source, tuple):
        src, code = code_or_source
        return _dispatch_legacy(src, code)
    return _dispatch(code_or_source)


# ============================================================
# 自动路由
# ============================================================

def _dispatch(code: str) -> pd.DataFrame:
    """根据代码前两位/前一位精确路由。优先级如下：

    场内 ETF/LOF：
      - 15xxxx / 16xxxx / 18xxxx → 深市
      - 5xxxxx                   → 沪市
    A 股：
      - 60xxxx / 68xxxx          → 沪市股票（含科创板）
      - 00xxxx / 30xxxx          → 深市股票（主板/创业板）
    其他 6 位数字（02xxxx / 12xxxx / 5xxxxx 中非 ETF 等）：
      - 视为场外基金（天天基金单位净值）
    sh######/sz######：
      - 视为指数，走新浪
    """
    code = code.strip()

    # 1. sh/sz 前缀 → 指数/ETF（新浪），原样传
    if re.fullmatch(r"(sh|sz)\d{6}", code, re.IGNORECASE):
        return _fetch_sina_daily(code.lower())

    # 2. 纯 6 位数字
    if re.fullmatch(r"\d{6}", code):
        prefix2 = code[:2]

        # 场内 ETF/LOF
        if prefix2 in ("15", "16", "18"):
            return _fetch_etf(code, market="sz")
        if prefix2 in ("50", "51", "52", "56", "58"):
            return _fetch_etf(code, market="sh")

        # 沪市/科创板股票（不与场外基金冲突）
        if prefix2 in ("60", "68"):
            return _fetch_a_stock(code)

        # 创业板股票
        if prefix2 == "30":
            return _fetch_a_stock(code)

        # 00xxxx 区间：深市主板股票（000001-003999）和场外基金（007xxx 等）共用，
        # 0/1/2/9 开头的纯数字代码绝大多数是场外基金。
        # 策略：先试场外基金，失败再 fallback 到 A 股。
        return _fetch_fund_then_stock(code)

    raise ValueError(
        f"无法识别的代码格式：{code!r}（应为 'sh######' / 'sz######' 或 6 位数字）"
    )


def _dispatch_legacy(src: str, code: str) -> pd.DataFrame:
    """向后兼容旧 config 的 (source_type, code) 元组写法。"""
    if src == "index_sina":
        return _fetch_sina_daily(code)
    if src == "fund_em":
        return _fetch_fund(code)
    if src == "etf":
        # 自动判断市场
        m = "sz" if code.startswith(("1",)) else "sh"
        return _fetch_etf(code, market=m)
    if src == "stock":
        return _fetch_a_stock(code)
    raise ValueError(f"未知数据源: {src}")


# ============================================================
# 各类数据源实现（统一返回 OHLCV 列、按日期升序）
# ============================================================

def _fetch_sina_daily(symbol: str) -> pd.DataFrame:
    """新浪指数/ETF 日线，symbol 形如 'sh000510' / 'sz159307'。"""
    import akshare as ak

    df = ak.stock_zh_index_daily(symbol=symbol)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df[_OHLCV]


def _fetch_etf(code: str, market: str) -> pd.DataFrame:
    """场内 ETF/LOF：先试新浪指数接口（K 线最稳），失败 fallback efinance/akshare 东财。"""
    import akshare as ak

    sym = f"{market}{code}"
    try:
        df = ak.stock_zh_index_daily(symbol=sym)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df[_OHLCV]
    except Exception as e_sina:
        # fallback：efinance
        try:
            import efinance as ef

            raw = ef.stock.get_quote_history(code, klt=101)
            df = raw.rename(columns={
                "日期": "date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume",
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df[_OHLCV]
        except Exception as e_ef:
            raise RuntimeError(
                f"ETF {code} 取数失败：sina={e_sina!r}; efinance={e_ef!r}"
            )


def _fetch_a_stock(code: str) -> pd.DataFrame:
    """A 股个股日线（akshare 东财，前复权）。"""
    import akshare as ak

    df = ak.stock_zh_a_hist(
        symbol=code, period="daily",
        start_date="20100101", end_date="20991231",
        adjust="qfq",
    )
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "最高": "high",
        "最低": "low", "收盘": "close", "成交量": "volume",
    })
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df[_OHLCV]


def _fetch_fund(code: str) -> pd.DataFrame:
    """场外基金单位净值（天天基金）。"""
    import akshare as ak

    df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    if "净值日期" not in df.columns or "单位净值" not in df.columns:
        raise RuntimeError(
            f"基金 {code} 返回字段异常：{df.columns.tolist()}"
        )
    df = df.rename(columns={"净值日期": "date", "单位净值": "close"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["open"] = df["close"]
    df["high"] = df["close"]
    df["low"] = df["close"]
    df["volume"] = 0
    return df[_OHLCV]


def _fetch_fund_then_stock(code: str) -> pd.DataFrame:
    """对前缀模糊的代码（00xxxx / 0xxxxx 等），先试场外基金，失败再退到 A 股。"""
    try:
        return _fetch_fund(code)
    except Exception as e_fund:
        try:
            return _fetch_a_stock(code)
        except Exception as e_stock:
            raise RuntimeError(
                f"代码 {code} 既不像场外基金也不像 A 股：fund={e_fund!r}; stock={e_stock!r}"
            )


# ============================================================
# 估值（仅展示，可选）
# ============================================================

def fetch_csindex_valuation(code: str) -> pd.DataFrame | None:
    """中证官网估值（PE/PB/股息率）。失败返回 None。"""
    try:
        import akshare as ak

        df = ak.stock_zh_index_value_csindex(symbol=code)
        df["日期"] = pd.to_datetime(df["日期"])
        return df.sort_values("日期").reset_index(drop=True)
    except Exception:
        return None
