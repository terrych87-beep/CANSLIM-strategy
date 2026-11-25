# CANSLIM（欧奈尔）A 股选股

与中枢 MACD / IF 策略 **完全隔离**：只产出筛选名单，不接入 `run_backtest.py`。

- 使用说明：[`../CANSLIM_使用说明.md`](../CANSLIM_使用说明.md)  
- 模块需求说明书：[`../CANSLIM_模块需求说明书.md`](../CANSLIM_模块需求说明书.md)  
- 口径台账：[`docs/plans/CANSLIM_落地步骤.md`](../../docs/plans/CANSLIM_落地步骤.md)

## 数据

| 用途 | 来源 |
|------|------|
| 日 K | 新浪 `CN_MarketData.getKLineData`（原始不复权） |
| 前复权 | **自建**：新浪分红表 + §7.2.1 公式 |
| 财务 C/A | 新浪 `getFinanceReport2022&source=gjzb` |
| 股票池 | 沪深300 JSON `node=hs300`；中证1000 HTML 翻页 |
| 基准 L/M | `sh000300` |

## 运行

在仓库根目录（需 `pandas`、`requests`）：

```powershell
# 建议使用本机已有解释器，例如：
# C:\ProgramData\miniconda3\python.exe

C:\ProgramData\miniconda3\python.exe -m screener.canslim.run_screen --codes 600519,000858,600000

# 全池（较慢，有限速与缓存）
C:\ProgramData\miniconda3\python.exe -m screener.canslim.run_screen --pool --out outputs/canslim_screen.csv

# 调试只跑 20 只
C:\ProgramData\miniconda3\python.exe -m screener.canslim.run_screen --pool --limit 20
```

缓存目录：`screener/canslim/cache/`（已 gitignore）。

## 评分

- X/4 = C·A·S·L 中 `pass=True` 的个数  
- 缺数 → `pass=None`（明细写 reason），**不当作 0**  
- M 为闸门（green/yellow/red），不进总分  
- N / I 首版不自动打分  

## 依赖

与中枢取数一致需要 `requests`；根目录 `requirements.txt` 已补充。
