# 中枢本地化迁移

`中枢本地化迁移` 是一个独立的本地量化工程，目标是将原先在聚宽平台上运行的“基于中枢的 MACD 交易逻辑交易策略验证”完整迁移到本地环境中，实现可独立运行、可回放、可验收、可持续迭代的工程体系。

## 项目目标

本项目的核心目标不是重新发明一套交易逻辑，而是把已经验证过的核心策略主干本地化，并把工程结构、数据层、事件引擎、状态管理、交易执行、报表输出和回测入口统一到同一个仓库中。

### 核心原则

1. **核心判断不动**
   - 中枢判断
   - range 构造
   - MACD 背离识别
   - 背离计数规则
   - 运行点顺序
   - 14:57 特殊规则
   - 09:00 日线边界
   - 已废弃的 MACD 突破开仓逻辑不恢复

2. **工程框架重建**
   - 数据读取层
   - 事件引擎
   - 状态管理
   - Broker / Portfolio
   - 报表输出
   - 回测入口
   - 移仓执行壳

3. **输出可对账**
   - 每个运行点有日志
   - 每次状态变化可追踪
   - 每笔交易可回放
   - 每轮回测有结果文件落盘

## 当前阶段

当前工程已经完成了基础骨架搭建，正在进入**系统化收口和验收阶段**。现阶段重点不是追求收益一致，而是确保：

- 数据读取正确
- 事件顺序固定
- 结构和 MACD 主干稳定
- 状态管理可追踪
- 交易逻辑可回放
- 输出文件可对账

## 目录结构

- `engine/`：数据、事件、账户、撮合、报表
- `strategy/`：结构模块、MACD 背离、状态管理、交易条件、策略控制器
- `data/`：行情 CSV 与特征表（本地）；**`data/README.md`** 说明本地辅助文件；**`实时数据运行.py` / `期货合约数据获取.py` / `bar_filters.py` 进 Git**
- `tools/`：辅助脚本（含 **`大盘指数爬取.py`** BBD 爬取，见 `tools/README.md`）
- `outputs/`：回测输出和验证结果
- `tests/`：模块测试、烟雾测试、短区间验证
- `quant_migration_governance/`：治理文档、边界说明、状态字典和验收清单
- `run_backtest.py`：统一回测入口
- `archives/baseline_20260624_v31/`：v3.1.0 基线说明归档
- `archives/baseline_20260620_v3/`：v3.0.0 基线说明归档
- `archives/baseline_20260607_v21/`：v2.1.0 历史基线归档
- `archives/baseline_20260606_v2/`：v2.0.0 历史基线归档
- `archives/baseline_20260604_171927/`：v1.0.0 历史基线归档

## 版本与回滚

当前成熟基线：**Git 标签 `v3.1.0-baseline-20260624`**。

| 版本 | 标签 | 说明文档 |
|------|------|----------|
| v3.1（当前） | `v3.1.0-baseline-20260624` | `quant_migration_governance/BASELINE_v3.1.md` |
| v3.0（历史） | `v3.0.0-baseline-20260620` | `quant_migration_governance/BASELINE_v3.md` |
| v2.1（历史） | `v2.1.0-baseline-20260607` | `quant_migration_governance/BASELINE_v2.1.md` |
| v2.0（历史） | `v2.0.0-baseline-20260606` | `quant_migration_governance/BASELINE_v2.md` |
| v1（历史） | `v1.0.0-baseline-20260604` | `quant_migration_governance/BASELINE.md` |

变更记录：`CHANGELOG.md`  
版本差异总览：**`V1-V3升级说明.md`**

- `data/`、`transaction.csv`、`outputs/` **不进 Git**（**例外**：`data/README.md`、`data/实时数据运行.py`、`data/期货合约数据获取.py`、`data/bar_filters.py`）；CSV 与回测产物需在本地自备

```powershell
git checkout v3.1.0-baseline-20260624
git checkout -b my-branch v3.1.0-baseline-20260624
git diff v3.0.0-baseline-20260620..v3.1.0-baseline-20260624
```

## 运行方式

### Python 环境（固定 miniconda3）

本项目统一使用 **miniconda3** 的 Python（含 `pandas`、`pyarrow` 等依赖）。请勿混用系统 Python 3.14，以免特征表 parquet 等依赖缺失。

推荐启动方式：

```powershell
# 网页回测（双击或命令行）
.\启动网页回测.bat
.\启动网页结构可视化.bat

# 任意脚本（在项目根目录）
.\env\run_with_conda.bat run_backtest.py --start-date 2021-01-01 --end-date 2022-01-01
.\env\run_with_conda.bat scripts\plot_structure.py --start-date 2014-01-01 --end-date 2026-06-05
.\env\run_with_conda.bat scripts\build_features.py --start-date 2012-01-04 --end-date 2026-06-05
```

Cursor / VS Code 已配置解释器：`%USERPROFILE%\miniconda3\python.exe`（见 `.vscode/settings.json`）。

首次或换机后安装依赖：

```powershell
.\env\run_with_conda.bat -m pip install -r requirements.txt
```

### 1. 运行测试

在项目根目录执行：

```bash
.\env\run_with_conda.bat -m pytest tests
```

如果本机没有安装 `pytest`，也可以先运行单个烟雾脚本进行快速验证：

```bash
.\env\run_with_conda.bat -m tests.test_short_run
```

### 2. 运行回测入口

```bash
.\env\run_with_conda.bat run_backtest.py --start-date 2020-06-01 --end-date 2020-06-30
```

默认数据目录为：

```text
G:\量化\中枢本地化迁移\data
```

如果需要手动指定数据目录：

```bash
.\env\run_with_conda.bat run_backtest.py --start-date 2020-06-01 --end-date 2020-06-30 --data-dir G:\量化\中枢本地化迁移\data
```

## 数据要求

项目运行依赖 `data/` 下的本地 CSV 文件，至少包括：

- `上证指数日线加入实时数据.csv`
- `上证指数30分钟加入实时数据.csv`
- `上证指数1分钟加入实时数据.csv`
- `IF_front_next_runpoints.csv`

如需更完整的短区间验证，建议补齐：

- `上证指数周线加入实时数据.csv`
- `上证指数5分钟加入实时数据.csv`

本地辅助（不进 Git，见 **`data/README.md`**）：`指数数据.txt`、`期货数据.txt`、`运行实时数据.bat` 等。

## 验收方式

本项目不以最终收益作为唯一验收标准，而是按治理文档逐项验收：

- 数据层是否可读、可对齐
- 事件顺序是否稳定
- 09:00 / 14:40 / 14:57 等特殊时点是否按规则执行
- 中枢 / range 结构是否稳定
- MACD 背离是否按固定口径识别
- 状态是否连续且可追踪
- 输出是否完整
- 短区间是否可稳定回放

详细验收项见：**`quant_migration_governance/BASELINE_v3.1.md`** 回归检查清单；治理目录说明见 **`quant_migration_governance/README.md`**。

## 当前推进重点

当前阶段优先保证以下链路可稳定运行：

1. 数据读取
2. 固定运行点事件
3. 结构模块
4. MACD 背离模块
5. 状态管理
6. 交易条件
7. 报表输出
8. 短区间验证

## 说明

本工程是一个单独项目，所有代码和数据都应放在 `G:\量化\中枢本地化迁移` 内完成。