# 基线版本 v3.1.0-baseline-20260624

本文件与 Git 标签 **`v3.1.0-baseline-20260624`** 成对使用，为当前**成熟基线**。  
v3.0：`BASELINE_v3.md`；v2.1：`BASELINE_v2.1.md`。

## 标签与代码

| 项 | 值 |
|----|-----|
| Git 标签 | `v3.1.0-baseline-20260624` |
| 打标日期 | 2026-06-24 |
| 父基线 | `v3.0.0-baseline-20260620` |
| 说明 | 未收盘 K 线过滤、IF 运行点、14:57 1m/30m；**v3.1 归档含工程清理与标准模块注释** |

```powershell
git show v3.1.0-baseline-20260624 --stat
git checkout v3.1.0-baseline-20260624
git diff v3.0.0-baseline-20260620..v3.1.0-baseline-20260624
```

## 相对 v3.0 变更摘要

### 未收盘 K 线过滤（P0 数据口径）

- **`data/bar_filters.py`**：共享模块 `latest_closed_bar_time` / `drop_future_and_unclosed` / `latest_closed_daily_date`。
- **分钟线**：仅保留 bar 结束时刻 ≤ 当前时刻（例：11:26 可取 11:00 的 30m，不可取 11:30）。
- **日线**：**15:00 前**不读、不写当日日 K（含行情源与 30m 聚合兜底）；`data_portal` 按查询时刻过滤。
- **写入链**：`data/实时数据运行.py` 抓取/合并/写 CSV 全路径过滤；未收盘时剔除 CSV 已有当日日线。
- **自检**：`scripts/verify_unclosed_bar_filter.py`（离线，不依赖网络）。

### IF 运行点同步（`data/期货合约数据获取.py`，进 Git）

- 东财 30m 失败 → **新浪 1m fallback**（换月日 IF2607/IF2608 等场景）。
- **同日补洞**：按已有时间点集合补全，不因 tail=14:57 跳过上午运行点。
- **13:00 运行点**：新浪 1m 午后首根为 13:01 时映射取价。
- **盘中当日**：日线历无当日（15:00 前）时，按工作日仍增量同步 IF。
- **运行点时刻**：仍为 8 档/日（10:00…11:00、13:00…14:57），**不含 11:30**（与策略 RUNPOINTS 一致）。

### 14:57 震荡突破价（A 类 / 特征表 v2）

- **`DataPortal.get_index_1m_close_at_1457`**：仅当日精确 14:57:00 1m bar，禁止 asof 回退。
- **策略 / 特征表**：有 1m 用 1m，否则 **30m fallback**；`FEATURE_SIGNAL_VERSION = 2`，变更后需 `--rebuild-features`。
- **聚宽 1m 补数工具**：`scripts/jq_export_kline.py`、`parse_jq_kline_log.py`、`merge_jq_export_1m.py`。

### 实时同步工程

- **`data/实时数据运行.py`**：末尾调用 IF 同步；`FORMAL_ENV_PATH`；`启动实时数据同步.bat`。
- **探测**：`scripts/probe_em_sina.py`（输出亦走未收盘过滤）。

### v3.1 工程归档（同 tag）

- **临时文件清理**：删除 `data/_*.txt`；个人 scratch 移至 `待移出项目/`（`.gitignore`）。
- **保留本地辅助**：`data/指数数据.txt`、`data/期货数据.txt`、`data/运行实时数据.bat`（见 `data/README.md`）。
- **`tools/大盘指数爬取.py`**：BBD 爬取（股宝 DDX）→ 根目录 `大盘资金流向.xlsx`；`data/大盘指数模板.xlsx` 为校验参照。
- **文档收口**：`quant_migration_governance/README.md`（仅维护 BASELINE*）；治理断链引用已改指向 BASELINE / cursor rules。
- **标准模块注释**：`data/`、`scripts/` 主线、入口脚本、对账工具按 `engine/data_loader.py` 格式补全；`scripts/README.md` 索引。

## 本地数据依赖

| 路径 | 进 Git | 说明 |
|------|--------|------|
| `data/实时数据运行.py` | 是 | 上证多周期增量 |
| `data/期货合约数据获取.py` | 是 | IF 运行点增量 |
| `data/bar_filters.py` | 是 | K 线未收盘过滤 |
| `data/README.md` | 是 | 本地辅助文件说明 |
| `tools/大盘指数爬取.py` | 是 | BBD 爬取 |
| `data/*.csv` | 否 | 行情与 IF 运行点 CSV |
| `data/features/` | 否 | 特征表 parquet |

## 回测证据（v3.1 参考）

30m 突破价 fallback 基线（用户验收，本地 outputs）：

`outputs/backtest/20260623_112914_20160101-20260623_R+17793.60pct/`

| 指标 | 值 |
|------|-----|
| 区间 | 2016-01-01 ~ 2026-06-23 |
| 收益率 | **+17793.60%** |
| 说明 | 14:57 缺 1m 时用 30m，避免 asof 假突破 |

>  naive 全量 1m @14:57 版本（`20260623_164330_...` +3924%）**勿作验收**——本地 1m 缺 14:57 导致 asof 失真。

## 回归检查清单

- [ ] `python scripts/verify_unclosed_bar_filter.py`：四项 OK
- [ ] 15:00 前跑 `data/实时数据运行.py`：日志含 1d 跳过 / 剔除当日日线；30m 不含未收盘 bar
- [ ] `python data/期货合约数据获取.py`：换月日可补全 8 档 IF 运行点
- [ ] 14:57 特征变更后：`python run_backtest.py --rebuild-features ...`
- [ ] 全区间回测与 v3.0 同杠杆下成交延续（无 2022 后 open_failed 冻结）

## 有意保留的 B/C 类口径

与 v3.0 相同：MACD 本地 `MIN_BAND_LEN=3` 等。权威口径见 `.cursor/rules/local-macd-reconciliation-baseline.mdc` 与 `strategy/macd_divergence.py` → `MacdDivCfg`。
