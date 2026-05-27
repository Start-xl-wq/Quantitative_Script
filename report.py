"""
报告层：终端 rich 表格 + markdown 归档。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import config as C
from advisor import Advice


def _color_for_label(label: str) -> str:
    if "熔断" in label or "暂缓" in label:
        return "red"
    if "重点" in label or "加码" in label:
        return "bold green"
    if "正常" in label:
        return "cyan"
    if "减半" in label:
        return "yellow"
    return "white"


def render_terminal(advices: list[Advice], valuations: dict[str, pd.DataFrame | None]) -> None:
    console = Console()

    # 摘要表
    summary = Table(
        title=f"📊 定投建议 · {datetime.now():%Y-%m-%d %H:%M}",
        show_lines=True,
    )
    summary.add_column("标的", style="bold")
    summary.add_column("申购代码")
    summary.add_column("最新日期")
    summary.add_column("收盘/净值", justify="right")
    summary.add_column("得分", justify="center")
    summary.add_column("建议", style="bold")
    summary.add_column("系数", justify="right")
    summary.add_column(f"金额(基准{C.BASE_AMOUNT})", justify="right")

    for a in advices:
        color = _color_for_label(a.label)
        summary.add_row(
            a.target_name,
            a.buy_code,
            a.last_date.strftime("%Y-%m-%d"),
            f"{a.last_close:.4f}",
            f"{a.score}/{getattr(a, 'score_max', 100)}",
            f"[{color}]{a.label}[/{color}]",
            f"{a.multiplier:.1f}x",
            f"[{color}]{a.amount:.0f}[/{color}]",
        )
    console.print(summary)

    # 每个标的的详细信号
    for a in advices:
        sig_table = Table(
            title=f"🔍 {a.target_name} 信号明细",
            show_lines=False,
        )
        sig_table.add_column("信号", style="bold")
        sig_table.add_column("触发", justify="center")
        sig_table.add_column("得分", justify="right")
        sig_table.add_column("当前值")
        sig_table.add_column("阈值")
        for d in a.details:
            mark = "[green]✓[/green]" if d.triggered else "[dim]·[/dim]"
            sig_table.add_row(d.name, mark, f"{getattr(d, 'points', 0):.1f}", d.value, d.threshold)
        console.print(sig_table)

        # 估值（仅展示）
        val_df = valuations.get(a.target_name)
        if val_df is not None and len(val_df) > 0:
            row = val_df.iloc[-1]
            console.print(Panel(
                f"日期 {row['日期']:%Y-%m-%d}  "
                f"PE1 [bold]{row['市盈率1']:.2f}[/bold]  "
                f"PE2 [bold]{row['市盈率2']:.2f}[/bold]  "
                f"股息率1 [bold]{row['股息率1']:.2f}%[/bold]  "
                f"股息率2 [bold]{row['股息率2']:.2f}%[/bold]",
                title=f"📐 {a.target_name} · 中证官网估值（仅参考）",
                border_style="blue",
            ))


def write_markdown(
    advices: list[Advice],
    valuations: dict[str, pd.DataFrame | None],
    out_dir: str | os.PathLike = C.REPORT_DIR,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = out_dir / f"report_{today}.md"

    lines: list[str] = []
    lines.append(f"# 定投建议报告 · {today}\n")
    lines.append(f"基准金额：每标的 **{C.BASE_AMOUNT}** 元 / 期\n")
    lines.append("\n## 摘要\n")
    lines.append(
        "| 标的 | 申购代码 | 最新日期 | 收盘/净值 | 得分 | 建议 | 系数 | 金额 |"
    )
    lines.append("|---|---|---|---:|:---:|---|---:|---:|")
    for a in advices:
        lines.append(
            f"| {a.target_name} | `{a.buy_code}` | "
            f"{a.last_date.strftime('%Y-%m-%d')} | "
            f"{a.last_close:.4f} | {a.score}/{getattr(a, 'score_max', 100)} | "
            f"**{a.label}** | {a.multiplier:.1f}x | "
            f"**{a.amount:.0f}** |"
        )

    for a in advices:
        lines.append(f"\n---\n\n## {a.target_name}（{a.buy_code}）\n")
        lines.append(f"- 数据日期：{a.last_date.strftime('%Y-%m-%d')}")
        lines.append(f"- 收盘/净值：**{a.last_close:.4f}**")
        lines.append(f"- 综合得分：**{a.score}/{getattr(a, 'score_max', 100)}**")
        lines.append(f"- 建议：**{a.label}**（{a.multiplier:.1f}x，金额 {a.amount:.0f} 元）")
        if a.circuit_breaker:
            lines.append("- ⚠️ 触发熔断：趋势空头 + 短期超买，强制暂缓。")

        lines.append("\n### 信号明细\n")
        lines.append("| 信号 | 触发 | 得分 | 当前值 | 阈值 |")
        lines.append("|---|:---:|---:|---|---|")
        for d in a.details:
            mark = "✅" if d.triggered else "·"
            lines.append(f"| {d.name} | {mark} | {getattr(d, 'points', 0):.1f} | {d.value} | {d.threshold} |")

        # 关键指标
        lines.append("\n### 关键指标\n")
        m = a.metrics
        lines.append(
            f"- MA200：{m['MA200']:.4f}\n"
            f"- MA200 偏离：{m.get('MA200偏离', 0)*100:.2f}%\n"
            f"- RSI(14)：{m['RSI14']:.2f}\n"
            f"- 近 1 年价格分位：{m['1Y分位']*100:.1f}%\n"
            f"- 距 60 日高点回撤：{m['60日回撤']*100:.2f}%\n"
            f"- 布林位置：{m.get('布林位置', 0):.2f}\n"
            f"- 布林下轨 / 上轨：{m['布林下轨']:.4f} / {m['布林上轨']:.4f}"
        )

        val_df = valuations.get(a.target_name)
        if val_df is not None and len(val_df) > 0:
            row = val_df.iloc[-1]
            lines.append("\n### 中证官网估值（仅参考，不参与打分）\n")
            lines.append(
                f"- 日期：{row['日期']:%Y-%m-%d}\n"
                f"- 市盈率1 / 市盈率2：{row['市盈率1']:.2f} / {row['市盈率2']:.2f}\n"
                f"- 股息率1 / 股息率2：{row['股息率1']:.2f}% / {row['股息率2']:.2f}%"
            )

    lines.append("\n---\n")
    lines.append(
        "_本报告为基于历史数据的量化提示，不构成投资建议。市场有风险，决策需独立。_\n"
    )

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
