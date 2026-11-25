# 变更日志

## 未发布 — CANSLIM 选股子系统（2026-11-25）

- 新增独立模块 `screener/canslim/`（**不接入**中枢 MACD / `run_backtest`）。
- 新浪取数：沪深300∪中证1000 成分、日 K、分红表、关键财务；**分红表自建前复权**（Q2=B）。
- CLI：`python -m screener.canslim.run_screen`；台账：`docs/plans/CANSLIM_落地步骤.md`。
- `requirements.txt` 补充 `requests`。
- **S4 全池**：沪深300∪中证1000 共 1067 只 → `outputs/canslim_screen_full.csv`；X/4≥3 子集 `..._min3.csv`。

---

## v3.1.0-baseline-20260624（2026-06-24）

相对 v3.0.0-baseline-20260620：未收盘 K 线过滤、IF 运行点增量修复、14:57 突破价 1m/30m、实时同步收口。

### 数据口径（P0）

- **`data/bar_filters.py`**：分钟线仅已收盘 bar；**15:00 前无当日日线**。
- **`data/实时数据运行.py`** / **`engine/data_portal.py`** / **`scripts/probe_em_sina.py`**：全链路过滤与写前剔除。
- **`scripts/verify_unclosed_bar_filter.py`**：离线自检。

### IF 运行点

- **`data/期货合约数据获取.py`** 进 Git：30m 失败 → 新浪 1m；同日补洞；13:00→13:01；盘中无日线历时仍同步。

### 14:57 突破价

- **`get_index_1m_close_at_1457`**：精确 1m，禁止 asof 回退；策略/特征表 30m fallback。
- **`FEATURE_SIGNAL_VERSION = 2`**；`--rebuild-features` 必用。
- **聚宽 1m 导出**：`scripts/jq_export_kline.py` 等。

### 工程

- **`启动实时数据同步.bat`**；`FORMAL_ENV_PATH` 修复；实时脚本末尾 IF 同步。
- **v3.1 归档**：清理 `data/_*.txt`；`待移出项目/`；`tools/大盘指数爬取.py`；标准模块注释；`data/scripts/tools README`；治理文档引用收口。

详见 `quant_migration_governance/BASELINE_v3.1.md`。

---

## v3.0.0-baseline-20260620（2026-06-20）

相对 v2.1.0-baseline-20260607：回测性能 P0/P1、实时数据增量同步、开仓手数修复、结构可视化拆分。

### 回测性能（P0）

- **`FeatureStore.get_div_info`**：signal 解析结果按 key 缓存；时间戳 `fromisoformat`，避免每运行点重复 `pd.to_datetime`（2021 全年回测 ~121s → **~9.6s**）。
- **`trading_conditions`**：`_as_timestamp_list` 跳过已是 `Timestamp` 的二次解析。

### 回测性能（P1）

- **`_debug_state` 默认关闭**：`DEBUG_STATE=True` 或 `BACKTEST_DEBUG_STATE=1` 开启。
- **智能 sync 双层跳过**：`run_backtest` 已覆盖区间不传 sync；`live_data_sync` 二次校验；`data/实时数据运行.py` 无新增时跳过 CSV 读写。

### 实时数据

- **`data/实时数据运行.py`**：纳入 Git；按 tail→当前动态 `datalen`；日 K 缺当日时用 30m 聚合补全；`live_data_sync` 调用。

### 开仓手数（A 类修复）

- **`_calc_open_lots`**：按 `保证金率 + 手续费率` 预留可用手数。
- **`Broker.open_position_with_retry`**：资金不足逐手减 1；策略开仓与移仓开新均走此路径。
- 修复高杠杆复利后 `open_failed` 导致权益曲线 2022 年后走平。

### 结构可视化

- **`scripts/structure_viz_core.py`**：自 `plot_structure.py` 拆出核心。
- **`parse_struct_params_from_mapping`**、`STRUCT_FLOAT_EPS` 表单字段。

详见 `quant_migration_governance/BASELINE_v3.md`。

---

## v2.1.0-baseline-20260607（2026-06-07）

相对 v2.0.0-baseline-20260606：平仓状态修复、MACD 前序柱、震荡多空分参、扫参工具与收益率图增强。

### 回测性能（P0）

- **智能跳过 sync**：`engine/data_coverage.py` 检查本地 CSV 是否覆盖回测区间；已覆盖则默认不跑 `live_data_sync`（`--force-sync-data` / 网页「强制同步」可覆盖）。
- **特征表指纹**：`identity_bundle_id` 仅 hash 结构/MACD 参数；sync 后同 bundle `extend` 新日期，不因 CSV mtime 重建。

### 回测性能（P1）

- **盘中结构冻结**：`before_market_open` 写入 `structure_bundle`（截止前一交易日收盘）；运行点只读，不重算中枢。
- **MACD 增量**：`IncrementalMacdSession` 滚动缓存 30m close，仅 append 新 bar；特征表 miss 或 `--no-features` 时自动 fallback。

### 文档

- 为核心 strategy/engine/scripts/入口/tests 模块补充标准注释（模块说明、输入参数、输出参数、用途、使用说明），对齐 `engine/data_loader.py` 约定。

### 策略与状态

- **`reset_after_close`**：移除平仓时对背离计数与 `osc_wait` 的清零（对齐聚宽 txt）。
- **开仓 `wait_bars`**：映射 `bars_elapsed` → 台账/日志；平仓保持空。

### MACD（B 类，用户确认）

- **`PREV_COMPARE_MIN_BAND_LEN`**：默认 4，前序同向柱 **`长度 >= 4`**（原逻辑等效为 `> 5`）。

### 震荡开仓（C 类，用户确认）

- **分设模型**：`OSC_RULES_MODEL_LONG=v1`，`OSC_RULES_MODEL_SHORT=v2`。
- **分设阈值**：多 0.9%/1.5%；空 0.5%/1%。
- **旧名兼容**：`OSC_RULES_MODEL`、`OSC_NEAR/BREAK_REF_THRESHOLD` 同时覆盖多空（扫参/CLI）。

### 特征表与可视化

- **`strategy/feature_store.py`**：预计算 `structure_daily` + `signals_runpoint` parquet；`resolve` / `extend`；扫参进程内共享。
- **`scripts/build_features.py`**：CLI 预构建特征表。
- **`scripts/plot_structure.py`**：全历史笔 + range/中枢框 + 悬停日结构 Tooltip（ECharts HTML）。

### 工程与可视化

- **`scripts/osc_param_sweep.py`**：贴近 0.2%→1.5%（步长 0.3%）× 突破 0.5%→2%（步长 0.5%）；共享 FeatureStore。
- **`scripts/analyze_sweep_close_pnl.py`**：扫参 trades 按持仓类型汇总平仓盈亏。
- **收益率走势图**：dataZoom 区间归零、Top3 回撤含日期、回撤区间竖线/色块。
- **网页调试**：双模型 + 四套震荡阈值；智能跳过 sync +「强制同步」勾选。
- **`requirements.txt`**、**`启动网页回测.bat`**：miniconda3 统一启动与依赖说明。

详见 `quant_migration_governance/BASELINE_v2.1.md`。

---

## v2.0.0-baseline-20260606（2026-06-06）

相对 v1.0.0-baseline-20260604 的里程碑更新：移仓修复、震荡开仓规则 v2、回测工程与可视化。

### 策略与引擎

- **移仓（`engine/contract_manager.py`）**：交割周触发 `_is_roll_week`；过期合约 `expired_force_roll` / `expired_force_close`。修复 v1 长回测在 2016 年后「僵尸持仓」、无法延续至 2026 的问题。
- **震荡开仓 v2（`strategy/trading_conditions.py`、`macd_divergence.py`、`state_manager.py`）**：
  - 废弃 `ref_close`；改用波段极值 `ref_low` / `ref_high`（底背离 `curr_min_price`、顶背离 `curr_max_price`）。
  - 贴近/突破约束见 `quant_migration_governance/BASELINE_v2.md`「震荡规则 v2 有意差异」。
  - **登记为 C 类有意差异**（相对聚宽 txt），不作为 v2 回归 bug。
- **MACD 本地口径**：`MacdDivCfg` 默认值未改（`MIN_BAND_LEN=3` 等），仍为 B 类对账基准。

### 回测入口与参数

- **`run_backtest.py`**：输出目录自动命名 `运行时间_回放区间_收益率`；`run_meta.json` 记录 `strategy_params`；支持 `--strategy-param KEY=VALUE`。
- **`strategy/backtest_params.py`**：震荡 / MACD / 结构参数规格与默认值。
- **`scripts/live_data_sync.py`**：封装 `data/实时数据运行.py` 的增量同步（可选，失败软降级）。

### 工具链

- **`tools/trade_visualizer.py`**、**`生成交易可视化.py`**：交易点图、收益率图；基准仅用上证日线 CSV。
- **`网页运行调试策略.py`**：子进程调用 `run_backtest.py`；策略参数表单（中文标签）。

### 验收 run（不进 Git，本地保留）

- `outputs/backtest/20260606_130020_20160101-20260606_R+2930.08pct/`
- 轻量归档：`archives/baseline_20260606_v2/`

### 对账说明

- v1 主验收：聚宽 `transaction.csv`（2021–2026，238 笔）↔ 本地，对齐率 171/238（71.8%）。
- v2 主验收：**全区间 2016–2026 可完整跑通**、移仓与持仓延续正常；聚宽流水对账为**参考**（起始年与震荡规则均已分叉，不宜用 v1 对齐率衡量 v2）。

---

## v1.0.0-baseline-20260604（2026-06-04）

P0–P2 聚宽交易逻辑对齐后首次固定基线。详见 `quant_migration_governance/BASELINE.md`。
