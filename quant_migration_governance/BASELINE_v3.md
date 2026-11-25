# 基线版本 v3.0.0-baseline-20260620

本文件与 Git 标签 **`v3.0.0-baseline-20260620`** 成对使用，为当前**成熟基线**。  
v2.1：`BASELINE_v2.1.md`；v2.0：`BASELINE_v2.md`；v1：`BASELINE.md`。

## 标签与代码

| 项 | 值 |
|----|-----|
| Git 标签 | `v3.0.0-baseline-20260620` |
| 打标日期 | 2026-06-20 |
| 父基线 | `v2.1.0-baseline-20260607` |
| 说明 | 回测性能 P0/P1、实时数据增量同步、开仓手数修复、结构可视化拆分 |

```powershell
git show v3.0.0-baseline-20260620 --stat
git checkout v3.0.0-baseline-20260620
git diff v2.1.0-baseline-20260607..v3.0.0-baseline-20260620
```

## 相对 v2.1 变更摘要

### 回测性能（P0）

- **`FeatureStore.get_div_info`**：解析 `close_index_json` 结果按 signal key **缓存**；时间戳用 `datetime.fromisoformat`，避免每运行点重复 `pd.to_datetime`（2021 全年回测由 ~121s 降至 **~9.6s**）。
- **`trading_conditions`**：若 `close_index` 已是 `Timestamp`，跳过二次解析。

### 回测性能（P1）

- **`_debug_state` 默认关闭**：`DEBUG_STATE=True` 或环境变量 `BACKTEST_DEBUG_STATE=1` 可开启。
- **智能 sync 双层跳过**：`run_backtest` 已覆盖区间不调用 sync；`live_data_sync` 传入区间时二次校验；`data/实时数据运行.py` 无新增时 **跳过全量 CSV 读写**（tail 读 + 按需写入）。

### 实时数据增量（`data/实时数据运行.py`，**已纳入 Git**）

- 按已有 **tail → 当前** 动态计算 `datalen`（上限 1024），非固定 30 条。
- **增量裁剪**：重叠 tail 仅保留 1 条对比；新增 bar 全留。
- **日线补全**：新浪日 K 缺当日时，由本地 + 增量 **30m** 聚合当日 OHLC。
- **调用链**：`run_backtest.py` → `scripts/live_data_sync.py` → 动态加载本脚本。

### 开仓手数（A 类修复）

- **`_calc_open_lots`**：可用手数按 `保证金率 + 手续费率`（`0.15 + 0.000023`）预留，与 `Portfolio.open_position` 一致。
- **`Broker.open_position_with_retry`**：资金不足时逐手减 1 重试（贴近聚宽「能开多少开多少」）；策略开仓与移仓开新都走此路径。
- **修复现象**：高杠杆 + 复利后权益极大时，原逻辑算满保证金未扣手续费 → 系统性 `open_failed` → 权益曲线 2022 年后走平（非收益率计算错误）。

### 结构可视化工程

- **`scripts/structure_viz_core.py`**：结构计算与 ECharts 渲染核心自 `plot_structure.py` 拆出。
- **`strategy/backtest_params.py`**：新增 `STRUCT_FLOAT_EPS` 表单字段；`parse_struct_params_from_mapping` 供结构可视化网页。

## 本地数据依赖

| 路径 | 进 Git | 说明 |
|------|--------|------|
| `data/实时数据运行.py` | **是** | 新浪增量同步主脚本 |
| `data/*.csv` | 否 | 上证 1m/5m/30m/1d/1w、IF 运行点等 |
| `data/features/` | 否 | 预计算特征表 parquet |
| `data/.env` | 否 | 密钥（参考 `.env.example`） |

## 回测证据（v3 正式验收）

`outputs/backtest/20260620_134610_20160101-20260620_R+264683.62pct/`

| 指标 | 值 |
|------|-----|
| 区间 | 2016-01-01 ~ 2026-06-20 |
| 开/平 | 236 / 235（`trade_count` 471） |
| 收益率 | **+264683.62%** |
| 末笔 | 2026-06-09 14:57 开多 IF2607 |
| sync | 已覆盖区间，跳过 |

> 误导 run（手数 bug）：`20260620_133147_...` 于 2022-06-15 后无成交，仅作修复前证据。  
> 性能验收：2021 全年、`sync` 已覆盖时约 **8–10s**。

## 回归检查清单

- [ ] `python run_backtest.py --start-date 2021-01-01 --end-date 2022-01-01 --no-sync-data`：约 10s 内完成
- [ ] 8 倍杠杆全区间：2022 年后仍有成交（无大量 `open_failed`）
- [ ] `python data/实时数据运行.py`：数据已最新时输出「各周期均无新增，跳过文件读写」
- [ ] 特征表命中：`data/features/{bundle_id}/` 覆盖回测区间
- [ ] 移仓周：`roll_count > 0`（长区间）

## 有意保留的 B/C 类口径

与 v2.1 相同：MACD 本地 `MIN_BAND_LEN=3` 等、震荡多空分参 C 类差异。权威口径见 `.cursor/rules/local-macd-reconciliation-baseline.mdc`。
