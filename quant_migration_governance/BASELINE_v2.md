# 基线版本 v2.0.0-baseline-20260606（历史）

> **当前成熟基线已升级为 v2.1**：见 [`BASELINE_v2.1.md`](BASELINE_v2.1.md) / 标签 `v2.1.0-baseline-20260607`。

本文件与 Git 标签 **`v2.0.0-baseline-20260606`** 成对使用。v1 保留作历史对照（`quant_migration_governance/BASELINE.md`）。

## 标签与代码

| 项 | 值 |
|----|-----|
| Git 标签 | `v2.0.0-baseline-20260606` |
| 打标日期 | 2026-06-06 |
| 父基线 | `v1.0.0-baseline-20260604` |
| 说明 | 移仓修复 + 震荡 ref_low/high + 回测工程与可视化 |

```powershell
git show v2.0.0-baseline-20260606 --stat
git checkout v2.0.0-baseline-20260606
git checkout -b my-fix v2.0.0-baseline-20260606
```

## 回测证据（不进 Git，本地保留）

| 项 | 路径 |
|----|------|
| 主验收 run | `outputs/backtest/20260606_130020_20160101-20260606_R+2930.08pct/` |
| 归档副本 | `archives/baseline_20260606_v2/` |
| 聚宽流水（本地） | `transaction.csv`（2021-01-04 ~ 2026-04-23，238 笔，不进 Git） |

### 验收摘要（对应该 run）

| 指标 | 值 |
|------|-----|
| 回放区间 | 2016-01-01 ~ 2026-06-06 |
| 开/平仓 | 各 231 笔（`trade_count` 462 含开平记录） |
| `total_return` | ≈ 29.30（+2930%） |
| 末笔交易 | 2026-04-23 平多 IF2605（与聚宽末笔同日同向，路径已长期分叉） |
| 移仓 | 交割周逻辑生效；无 v1 式十年僵尸持仓 |

> 本 run 的 `run_meta.json` 中 `OSC_NEAR_REF_THRESHOLD=0.015` 为当次网页/CLI 覆盖值；代码默认仍为 `0.004`（见 `strategy/backtest_params.py`）。

## v2 主验收标准（相对 v1）

1. **全区间可跑通**：2016 → 2026 持仓与交易连续，不因移仓 bug 中断。
2. **震荡规则 v2**：按本文「C 类」登记，不强行对齐聚宽 txt。
3. **MACD B 类**：`MacdDivCfg` 本地参数不变，规则见 `BASELINE.md` 与 `.cursor/rules/local-macd-reconciliation-baseline.mdc`。
4. **聚宽流水对账**：仅作参考；本地自 2016 年起交易，与 `transaction.csv`（2021 起）路径必然分叉，**不得**用 v1 的 171/238 对齐率考核 v2。

## 震荡规则 v2 有意差异（C 类）

相对聚宽策略 txt / v1 本地 `ref_close` 口径，v2 **有意**采用下列规则（用户 2026-06-06 确认登记）：

| 项 | v1 / 聚宽倾向 | v2 本地 |
|----|----------------|---------|
| 参考价 | 等待期起点 `ref_close` | 底背离波段 `ref_low`、顶背离波段 `ref_high` |
| 震荡多「贴近」 | （依 ref_close） | `px/ref_low - 1 ≤ OSC_NEAR_REF_THRESHOLD`（涨幅未超阈值） |
| 震荡多「突破约束」 | — | `min(等待期收盘)/ref_low - 1 ≥ -OSC_BREAK_REF_THRESHOLD` |
| 震荡空「贴近」 | （依 ref_close） | `(ref_high - px)/ref_high ≤ OSC_NEAR_REF_THRESHOLD`（跌幅未超阈值） |
| 震荡空「突破约束」 | — | `max(等待期收盘)/ref_high - 1 ≤ OSC_BREAK_REF_THRESHOLD` |

实现位置：

- `strategy/macd_divergence.py`：写入 `osc_wait.long/short` 的 `ref_low` / `ref_high`
- `strategy/trading_conditions.py`：开仓判定
- `strategy/state_manager.py`：移除 `ref_close` 字段

默认阈值（可被 `--strategy-param` / 网页覆盖）：

- `OSC_NEAR_REF_THRESHOLD`：0.004（0.4%）
- `OSC_BREAK_REF_THRESHOLD`：0.01（1%）
- `BREAKTHROUGH_WINDOW_BARS` / `OSC_WAIT_BARS`：18

## 移仓修复摘要

| 问题（v1） | 修复（v2） |
|------------|------------|
| 第三个周五遇节假日仅 `front` 可移仓，长期卡死旧合约 | 交割周 `_is_roll_week` 触发移仓 |
| 过期合约无强制处理 | `expired_force_roll` / `expired_force_close` |

代码：`engine/contract_manager.py`、`strategy/center_macd_strategy.py`（force_close 分支）。

## 工程能力（v2 新增）

- 回测目录：`{时间}_{区间}_R{收益率}pct`（`run_backtest.py`）
- 参数落盘：`run_meta.json` → `strategy_params`
- 可视化：`python 生成交易可视化.py <run_dir>`
- 网页调试：`python 网页运行调试策略.py` → http://127.0.0.1:8765

## 回归检查清单

改 `strategy/` 或 `engine/` 后建议：

1. 短区间冒烟 + 必要时全量 2016–至今。
2. 确认末笔日期接近 `end_date`，无长期无交易空窗（移仓回归）。
3. 勿为对齐聚宽修改 MACD B 类参数或撤销震荡 C 类规则，除非用户明确决策。
4. 可选：对重叠区间（2021–2026）跑 `compare_trades_structural.py` 作参考，不作为 v2 硬性门禁。

## 仓库外依赖

同 v1：`data/` CSV、`transaction.csv`、`outputs/` 需在本地自备。
