# 基线版本 v2.1.0-baseline-20260607

本文件与 Git 标签 **`v2.1.0-baseline-20260607`** 成对使用，为当前**成熟基线**。  
v2.0：`BASELINE_v2.md`；v1：`BASELINE.md`。

## 标签与代码

| 项 | 值 |
|----|-----|
| Git 标签 | `v2.1.0-baseline-20260607` |
| 打标日期 | 2026-06-07 |
| 父基线 | `v2.0.0-baseline-20260606` |
| 说明 | 特征表、智能 sync、盘中结构冻结/MACD 增量、结构可视化、震荡多空分参、扫参/汇总、文档 |

```powershell
git show v2.1.0-baseline-20260607 --stat
git checkout v2.1.0-baseline-20260607
git diff v2.0.0-baseline-20260606..v2.1.0-baseline-20260607
```

## 相对 v2.0 变更摘要

### 策略 / 状态（A 类修复）

- **`reset_after_close`**：平仓后**不再**调用 `reset_divergence_state()` / `reset_wait_state()`，对齐聚宽；修复止损/止盈后顶底计数被误清零。
- **开仓 `wait_bars`**：从 `open_decision.extra.bars_elapsed` 写入台账与日志；**平仓留空**。

### MACD（B 类口径微调，用户确认）

- **`PREV_COMPARE_MIN_BAND_LEN`**：默认 **4**；前序同向柱 **`长度 >= 4`**。

### 震荡开仓（C 类有意差异，用户确认）

多空**分设规则模型**与**分设阈值**（`strategy/backtest_params.py` 默认值）：

| 参数 | 震荡多默认 | 震荡空默认 |
|------|------------|------------|
| `OSC_RULES_MODEL_LONG` / `SHORT` | `v1`（`ref_close`） | `v2`（`ref_high`） |
| `OSC_NEAR_REF_THRESHOLD_LONG` / `SHORT` | `0.009`（0.9%） | `0.005`（0.5%） |
| `OSC_BREAK_REF_THRESHOLD_LONG` / `SHORT` | `0.015`（1.5%） | `0.01`（1%） |

**CLI / 扫参兼容**（同时覆盖多空）：

- `OSC_RULES_MODEL` → 写入 `OSC_RULES_MODEL_LONG` 与 `SHORT`
- `OSC_NEAR_REF_THRESHOLD` / `OSC_BREAK_REF_THRESHOLD` → 写入四套阈值

突破多/空逻辑**未改**（见 `trading_conditions.check_open/check_close`）。

### 回测性能（P0 / P1）

- **智能跳过 sync**：`engine/data_coverage.py`；本地已覆盖区间则默认不跑 `live_data_sync`（`--force-sync-data` / 网页「强制同步」可覆盖）。
- **特征表**：`strategy/feature_store.py` + `scripts/build_features.py`；指纹仅 hash struct/macd；`extend` 追加缺失日。
- **盘中结构冻结 + MACD 增量**：9:00 写入 `structure_bundle`（昨收止）；`IncrementalMacdSession` 滚动 append；特征表 miss 时 fallback。

### 特征表与结构可视化

- **`data/features/{bundle_id}/`**：`structure_daily.parquet`、`signals_runpoint.parquet`（30m 背离 + 1m 顶背离 + break/in_range）。
- **`scripts/plot_structure.py`**：全历史笔、pending 虚线、range/中枢状态片段框、悬停日 Tooltip。

### 工程 / 工具

- **`scripts/osc_param_sweep.py`**：贴近网格 0.2%→1.5% 步长 0.3%；突破 0.5%→2% 步长 0.5%；进程内共享 FeatureStore。
- **`scripts/analyze_sweep_close_pnl.py`**：按 OSC 参数 × 持仓类型汇总平仓次数/盈亏。
- **收益率走势图**（`tools/trade_visualizer.py`）：dataZoom 区间归零、Top3 回撤日期、回撤区间标注。
- **网页调试**：双模型 + 四套阈值；允许 sync（覆盖则跳过）+ 强制同步勾选。
- **`requirements.txt`**、**`启动网页回测.bat`**：conda 环境与网页启动。

## 回测证据

v2.0 主验收 run 仍作历史对照：

`outputs/backtest/20260606_130020_20160101-20260606_R+2930.08pct/`

扫参汇总（本地）：

`outputs/param_sweep/osc_grid_20260606_184626/close_pnl_by_position_type.csv`

> v2.1 含状态、MACD 与震荡默认参数变更，**成交流水与 v2.0 分叉**；正式验收请用 v2.1 代码重跑全区间。

## 回归检查清单

1. 平仓后顶/底背离计数不因止损/止盈清零。
2. 开仓台账含 `等待bar数`（平仓为空）。
3. `2016-10-19 13:00` 本地 `has_top=False`（与聚宽探针一致）。
4. 默认震荡：多 v1 + 0.9%/1.5%，空 v2 + 0.5%/1%。
5. 收益率图：缩放区间收益率从 0% 起；回撤竖线仅框累计收益率曲线。
6. 回测区间数据已覆盖时：`[sync-data] 跳过`；特征表命中全量 bundle 子集加载。
7. `--no-features` 与默认特征表短区间回测：收益率与成交笔数一致（增量 MACD 与全量等价）。

## 仓库外依赖

同 v2：`data/`、`transaction.csv`、`outputs/` 本地自备。
