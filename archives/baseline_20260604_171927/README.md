# 基线回测归档 v1.0.0-baseline-20260604

本目录为 `outputs/backtest/run_20260604_171927` 的**轻量归档**，供对账与回归时对照。

完整 run（含 `state_snapshots.csv`、`run_log.log`）仍在：

`G:\量化\中枢本地化迁移\outputs\backtest\run_20260604_171927\`

## 本目录文件

| 文件 | 说明 |
|------|------|
| `trades.csv` | 成交明细 |
| `summary.json` | 回测汇总 |
| `METRICS.txt` | 对账指标摘要 |

## 复现对账

聚宽流水使用仓库外的 `transaction.csv`（项目根目录，不进 Git）。

```powershell
python compare_trades_structural.py outputs/backtest/run_20260604_171927/trades.csv
```

与归档对比时，可将 `compare_trades_structural.py` 中 `LOCAL_PATH` 临时改为本目录的 `trades.csv`。
