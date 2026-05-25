# tt_py · 定投建议小工具

为天天基金渠道下的两只标的（中证 A500、红利低波）每天给出**该买多少**的量化建议，
不用看 K 线，直接看结论。

## 设计思路

- **数据源**：akshare（新浪指数日线 + 中证官网估值）+ efinance/akshare（场外基金净值）
- **指标**：pandas-ta（RSI / SMA / Bollinger）
- **策略**：5 个独立信号 → 总分 → 金额系数 → 实际金额
- **输出**：rich 终端表格 + 每日 markdown 归档（`reports/`）

## 5 个信号（每个 1 分，满分 5）

| 信号 | 触发条件 | 含义 |
|---|---|---|
| 趋势 | 收盘 > MA200 | 大趋势向上 |
| 估值/位置 | 近 1 年价格分位 ≤ 40% | 偏便宜 |
| 超跌 | RSI(14) ≤ 35 | 短期超跌 |
| 回撤 | 距 60 日高点回撤 ≥ 5% | 让利窗口 |
| 极端超跌 | 收盘 ≤ 布林下轨（20, 2σ） | 短期极端 |

## 评分 → 金额

| 总分 | 建议 | 系数 | 金额（基准 1000）|
|---|---|---|---|
| 0 | 暂缓 | 0x | 0 |
| 1 | 减半定投 | 0.5x | 500 |
| 2 | 正常定投 | 1.0x | 1000 |
| 3 | 加码定投 | 1.5x | 1500 |
| 4–5 | 重点加仓 | 2.0x | 2000 |

**风险熔断**：价格 < MA200 且 RSI ≥ 70（趋势空头还超买）→ 强制 0x。

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install efinance akshare pandas-ta rich
```

## 使用

```bash
.venv/bin/python main.py
```

输出：
- 终端：彩色摘要表 + 每个标的的信号明细 + 中证官网估值
- 文件：`reports/report_YYYY-MM-DD.md`

## 配置

所有可调参数都在 `config.py`：

```python
TARGETS = [
    {"name": "中证A500", "code": "022430", "valuation_csindex": "000510"},
    {"name": "红利低波", "code": "007466", "valuation_csindex": "H30269"},
    {"name": "国证A500 ETF", "code": "159307"},
]
```

只需要填 `code`，类型自动识别：

| code 形式 | 例子 | 路由 |
|---|---|---|
| `sh######` / `sz######` | `sh000510` | 指数（新浪日线） |
| `15xxxx` / `16xxxx` / `18xxxx` | `159307` | 深市 ETF/LOF |
| `5xxxxx` | `512890` | 沪市 ETF |
| `60xxxx` / `68xxxx` / `30xxxx` | `600519` | A 股个股 |
| 其他 6 位数字 | `022430` / `007466` | 场外基金（先试基金，失败 fallback A 股） |

`name` 可选，没填默认用 `code`。`valuation_csindex` 也可选，填中证官网指数代码就在报告里加 PE/PB/股息率展示，国证/其他指数不填即可。

其他参数：
- `BASE_AMOUNT`：基准金额，默认 1000
- 各信号阈值：MA 周期、分位窗口、RSI 阈值、回撤阈值、布林参数
- `SCORE_TO_MULT`：评分到系数的映射
- `ENABLE_CIRCUIT_BREAKER`：风险熔断开关

## 自动化（可选）

每个交易日 15:30 自动跑：

```bash
crontab -e
# 加入：
30 15 * * 1-5 cd /home/x-dche/tt_py && .venv/bin/python main.py >> cron.log 2>&1
```

## 项目结构

```
tt_py/
├── config.py     # 标的、阈值、金额，全部可改
├── data.py       # 取数（akshare/efinance 薄封装）
├── advisor.py    # 5 信号 → 打分 → 建议金额
├── report.py     # 终端 rich + markdown 归档
├── main.py       # 入口
├── reports/      # 每日报告归档
└── .venv/        # 虚拟环境
```

## 关于数据源的小坑

- **东财直连**（efinance + akshare 的东财源）当前从本机连不通，已切到新浪源 + 中证官网。
- **中证 A500 指数**：用 akshare `stock_zh_index_daily('sh000510')`，5000+ 条历史，稳定。
- **红利低波**：中证指数 `H30269` 在新浪不可拉，改用申购基金 `007466` 单位净值做技术信号代理（净值就是它跟踪指数的近似），1600+ 条历史够用。
- **估值（PE/PB/股息率）**：用 akshare `stock_zh_index_value_csindex`，从中证官网拉，仅展示不参与打分。

## 后续可加（二期）

- xalpha 集成：把你实际的定投流水（csv）灌进去，自动算 IRR、当前持仓收益、再平衡建议
- 钉钉/邮件/微信推送
- 信号回测：把当前规则跑过去 5 年的数据，看 IRR vs 简单定投，校准阈值

---

_本工具为量化提示，不构成投资建议。市场有风险，决策需独立。_
