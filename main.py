"""
入口：拉数据 → 评估 → 输出终端 + markdown 归档。

用法：
    .venv/bin/python main.py
"""

from __future__ import annotations

import re
import sys

from rich.console import Console

import config as C
from advisor import evaluate
from data import fetch_csindex_valuation, fetch_price
from report import render_terminal, write_markdown


def _fetch_name(code: str) -> str | None:
    """尝试通过 akshare 自动获取基金/ETF 的名称。失败返回 None。"""
    try:
        import akshare as ak

        code = code.strip()
        # 纯 6 位数字 → 尝试雪球基金信息接口
        if re.fullmatch(r"\d{6}", code):
            df = ak.fund_individual_basic_info_xq(symbol=code)
            row = df[df["item"] == "基金名称"]
            if not row.empty:
                return str(row["value"].values[0]).strip()
    except Exception:
        pass
    return None


def _normalize_target(tgt: dict) -> dict:
    """允许 TARGETS 用新简写（{code, name?, valuation_csindex?}）
    或老格式（{name, buy_code, price_source}）。统一返回新格式。
    当 name 未填写时，自动从网络查询真实基金/ETF 名称。"""
    if "code" in tgt:
        name = tgt.get("name")
        if not name:
            name = _fetch_name(tgt["code"]) or tgt["code"]
        return {
            "name": name,
            "code": tgt["code"],
            "valuation_csindex": tgt.get("valuation_csindex"),
        }
    # 兼容老格式
    if "buy_code" in tgt and "price_source" in tgt:
        # 老格式中 price_source 是 (src, code) 元组，主要做向后兼容
        return {
            "name": tgt.get("name") or tgt.get("buy_code") or "?",
            "code": tgt["buy_code"],
            "valuation_csindex": tgt.get("valuation_csindex"),
            "_legacy_price_source": tgt["price_source"],
        }
    raise ValueError(f"无法识别的 TARGET 配置：{tgt!r}（需要至少包含 'code' 字段）")


def main() -> int:
    console = Console()
    advices = []
    valuations: dict[str, object] = {}

    for raw in C.TARGETS:
        tgt = _normalize_target(raw)
        name = tgt["name"]
        code = tgt["code"]

        console.print(f"[dim]→ 拉取 {name} ({code}) ...[/dim]")
        try:
            if "_legacy_price_source" in tgt:
                df = fetch_price(tgt["_legacy_price_source"])
            else:
                df = fetch_price(code)
        except Exception as e:
            console.print(f"[red]× {name} 拉数失败：{e}[/red]")
            continue

        try:
            advice = evaluate(df, name, code)
            advices.append(advice)
        except Exception as e:
            console.print(f"[red]× {name} 评估失败：{e}[/red]")
            continue

        val_code = tgt.get("valuation_csindex")
        if val_code:
            valuations[name] = fetch_csindex_valuation(val_code)

    if not advices:
        console.print("[red]没有任何标的可以评估，退出。[/red]")
        return 1

    console.print()
    render_terminal(advices, valuations)

    path = write_markdown(advices, valuations)
    console.print(f"\n[green]✓ 报告已写入：{path}[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
