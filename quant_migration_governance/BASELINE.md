# 基线版本 v1.0.0-baseline-20260604（历史）

> **当前成熟基线已升级为 v2.1**：见 [`BASELINE_v2.1.md`](BASELINE_v2.1.md) / 标签 `v2.1.0-baseline-20260607`。  
> 本文件保留 v1 对账证据与 MACD B 类说明，供与聚宽 2021 起流水对照时回滚参考。

本文件与 Git 标签 **`v1.0.0-baseline-20260604`** 成对使用。

## 标签与代码

| 项 | 值 |
|----|-----|
| Git 标签 | `v1.0.0-baseline-20260604` |
| 打标日期 | 2026-06-04 |
| 说明 | P0–P2 聚宽交易逻辑对齐后首次固定基线 |

打标后查看 commit：

```powershell
git show v1.0.0-baseline-20260604 --stat
```

回滚到该版本（只读检查）：

```powershell
git checkout v1.0.0-baseline-20260604
```

从基线拉新分支继续改：

```powershell
git checkout -b my-fix v1.0.0-baseline-20260604
```

## 回测证据（不进 Git，本地保留）

| 项 | 路径 |
|----|------|
| 主回测 run | `outputs/backtest/run_20260604_171927/` |
| 归档副本（关键文件） | `archives/baseline_20260604_171927/` |
| 聚宽流水（本地） | `transaction.csv`（仓库外） |

### 验收摘要（对应该 run）

- 聚宽成交：238 笔（`transaction.csv`）
- 本地成交：218 笔（不含移仓）
- 完全对齐（时间+方向+合约+价格±0.5）：**171 / 238（71.8%）**
- 路径分叉：38 段；对齐后字段偏差：**0**
- `summary.json`：`total_return` ≈ 10.07（+1007%）

## 对账方式

```powershell
python compare_trades_structural.py
# 或指定 run：
python compare_trades_structural.py outputs/backtest/run_20260604_171927/trades.csv
```

默认本地路径指向 `run_20260604_171927/trades.csv`。

## 对账基准（勿为对齐聚宽改回）

- MACD 以**本地参数**为准（`strategy/macd_divergence.py` 中 `MacdDivCfg`）。
- 与聚宽流水在 MACD 字段上的差异，登记为 **B 类**，不视为 bug。
- 规则/时点类差异为 **A 类**，需对照 `strategy/trading_conditions.py` 与 IF 价取法逐项查。

本地 MACD 关键参数（相对聚宽 txt 有意差异）：

| 参数 | 本地 | 聚宽 txt |
|------|------|----------|
| MIN_BAND_LEN | 3 | 5 |
| MIN_PRICE_MOVE | 0.0 | 0.0005 |
| PREV_COMPARE_MIN_BAND_LEN | 4（前序柱长度 >= 4） | （无） |

## 已知登记差异（示例）

- **#2** `2021-03-03` 震荡多：用户确认本地正确。
- **#14** `2023-01-10` 聚宽震荡空 vs 本地空仓后 `2023-01-16` 14:57 突破多：B 类 + 01-16 重新对齐。
- **#22** `2023-11-30` 聚宽震荡多 vs 本地 `2024-02-01` 突破空：B 类 + 02-01 10:30 重新对齐。

## 回归检查清单

改 `strategy/` 或 `engine/` 后建议：

1. 跑短测或全量回测，生成新 `outputs/backtest/run_*`。
2. 运行 `compare_trades_structural.py` 与基线 run 对比。
3. 对齐率不应无故低于 171/238；新增 A 类分叉需有文档说明。
4. 勿为提升对齐率修改上述 MACD 本地参数，除非用户明确决策。

## 仓库外依赖

- `data/` 目录下 CSV（日线/分钟/运行点等）需在本地自备。
- `transaction.csv` 用于与聚宽流水对账，需放在项目根目录。
