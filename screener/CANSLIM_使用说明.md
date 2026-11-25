# CANSLIM（欧奈尔）选股 — 使用说明

> 位置：`screener/canslim/`（与中枢 MACD / IF 策略**完全隔离**，只出名单，不自动下单）  
> 口径台账：`docs/plans/CANSLIM_落地步骤.md`  
> 模块串联需求：同目录 [`CANSLIM_模块需求说明书.md`](./CANSLIM_模块需求说明书.md)  
> PDF：`CANSLIM_使用说明.pdf`、`CANSLIM_模块需求说明书.pdf`  
> 全池明细 Excel：`CANSLIM_全池评分明细.xlsx`（含「全池明细 / X≥3 / X=4 / 字段说明」）

---

## 1. 策略在做什么

基于威廉·欧奈尔 **CANSLIM** 思路，对 A 股候选池做**可复现的量化打分**，输出研究用筛选表。

| 字母 | 含义（本实现） | 通过条件（已确认） |
|------|----------------|--------------------|
| **C** | 当季成长：扣非净利润同比 | ≥ **25%** |
| **A** | 年度质量：净利同比 + ROE | 净利同比 ≥ **25%** 且 ROE ≥ **15%** |
| **S** | 供需/量能：近 20 日均量 ÷ 此前 60 日均量 | ≥ **1.2** |
| **L** | 相对强度：近约 6 个月相对沪深300超额收益 | ≥ **20%** |
| **M** | 大盘闸门（不进总分） | 沪深300 相对 MA50/MA200 → green / yellow / red |
| **N** | 新产品/新高 | **不自动打分**（人工）；表中可有近 52 周高辅助列 |
| **I** | 机构 | **首版跳过** |

**总分**：`X_of_4` = C·A·S·L 中 `pass=True` 的个数（0～4）。  
**缺数**：`pass` 为空 / 明细写原因，**禁止把缺数当 0**。  
**M=red**：防守环境；名单可看，不宜当进攻买入信号。

数据主源：**新浪**。日 K 接口为不复权，本策略用**分红表自建前复权**后再算 S/L。

股票池默认：**沪深300 ∪ 中证1000**（去重，约 1000+ 只）。

---

## 2. 怎么运行

在仓库根目录 `中枢本地化迁移` 下执行（需已安装 `pandas`、`requests`）。

### 2.1 推荐解释器

```powershell
C:\ProgramData\miniconda3\python.exe -m screener.canslim.run_screen --help
```

（若本机 `python` 已配置好，也可直接 `python -m screener.canslim.run_screen ...`。）

### 2.2 常用命令

```powershell
# 指定几只票（代码请加引号，避免 PowerShell 把 000858 吃成 858）
C:\ProgramData\miniconda3\python.exe -m screener.canslim.run_screen --codes "600519,000858,600000"

# 全池（沪深300∪中证1000）
C:\ProgramData\miniconda3\python.exe -m screener.canslim.run_screen --pool --out outputs/canslim_screen_full.csv

# 只跑前 N 只（调试）
C:\ProgramData\miniconda3\python.exe -m screener.canslim.run_screen --pool --limit 20

# 断点续跑（跳过输出 CSV 里已成功的代码）
C:\ProgramData\miniconda3\python.exe -m screener.canslim.run_screen --pool --resume --out outputs/canslim_screen_full.csv

# 结束时额外导出 X/4≥3 的子集（全量仍会写）
C:\ProgramData\miniconda3\python.exe -m screener.canslim.run_screen --pool --min-x 3 --out outputs/canslim_screen_full.csv

# 强制不走本地缓存（重新拉新浪）
C:\ProgramData\miniconda3\python.exe -m screener.canslim.run_screen --pool --no-cache --out outputs/canslim_fresh.csv
```

### 2.3 主要参数

| 参数 | 说明 |
|------|------|
| `--codes` | 逗号分隔六位代码 |
| `--pool` | 使用沪深300∪中证1000 |
| `--out` | 输出 CSV 路径；默认 `outputs/canslim_screen.csv` |
| `--limit` | 最多处理 N 只 |
| `--resume` | 续跑 |
| `--no-cache` | 禁用 `screener/canslim/cache/` |
| `--min-x` | 另存一份 `X_of_4 ≥ N` 的过滤表 |
| `--flush-every` | 每 N 只刷盘一次（默认 10） |

---

## 3. 看哪个表（输出与缓存）

### 3.1 结果表（给人看）

| 文件 | 说明 |
|------|------|
| `outputs/canslim_screen_full.csv` | 全池最近一次完整结果（若你按上文 `--out` 命名） |
| `outputs/canslim_screen_full_min3.csv` | X/4≥3 子集（人工从全量表筛出或 `--min-x 3` 生成） |
| `outputs/canslim_screen.csv` | 未指定 `--out` 时的默认文件名 |

用 Excel / WPS / VS Code 打开即可。注意用「文本」方式看待 `code` 列，避免前导零丢失；程序写盘时会尽量写成六位字符串。

### 3.2 结果表关键列

| 列 | 含义 |
|----|------|
| `code` / `name` / `pool` | 代码、名称、所属池（HS300 / CSI1000 / 并集标记） |
| `X_of_4` / `known_of_4` | 通过数 / 有明确 True/False 的因子数 |
| `C_pass` / `C_value` / `C_detail` | C 是否通过、扣非同比、说明 |
| `A_pass` / `A_net_yoy` / `A_roe` / `A_detail` | A 是否通过、净利同比、ROE、说明 |
| `S_pass` / `S_value` / `S_detail` | 量比 |
| `L_pass` / `L_value` / `L_detail` | 6 月超额 |
| `M_gate` / `M_detail` | 大盘闸门 green/yellow/red |
| `near_52w_high` | 辅助：现价/近 52 周高（前复权） |
| `last_close_qfq` | 最新前复权收盘 |
| `qfq_warnings` | 复权过程告警（一般为空） |
| `error` | 单票失败原因；空表示成功 |

**怎么读名单**：先看 `M_gate`；再按 `X_of_4` 降序；点开 `*_detail` 看不过的原因。X=4 且 M≠red 才更接近「可研究买入候选」（仍非交易指令）。

### 3.3 本地缓存（机器用，一般不用手开）

目录：`screener/canslim/cache/`（已 gitignore）

| 前缀 | 内容 |
|------|------|
| `cons_*` | 成分股列表 |
| `kline_*` | 日 K |
| `bonus_*` | 分红事件 |
| `fin_*` | 财务关键指标 |

---

## 4. 是否自动更新？

**不会自动更新。**

- 没有定时任务、没有挂到中枢 `run_backtest` / 实时同步。  
- 每次名单以你**手动跑 CLI** 的时刻为准。  
- 若存在缓存，默认会复用缓存，**不等于**盘后自动刷新。

---

## 5. 手动如何更新数据

| 目的 | 做法 |
|------|------|
| 刷新全池名单（推荐日常） | 再跑一遍 `--pool --out ...`；若怀疑缓存旧，加 `--no-cache` |
| 只刷新失败/未跑完的 | `--pool --resume --out 同一路径` |
| 清空缓存后全量重拉 | 删除 `screener/canslim/cache/` 下文件，或 `--no-cache` |
| 只更新几只票 | `--codes "......"` 写出到临时 CSV，再自行合并 |
| 成分股变更 | 删掉 `cache/cons_*` 后重跑 `--pool`（会重新拉新浪成分） |

财务与行情均在跑分时按票拉取；**没有**单独的「只更新数据库、不打分」入口——更新即重新跑选股。

---

## 6. 依赖与边界

- Python 依赖：`pandas`、`requests`（见仓库根目录 `requirements.txt`）。  
- 网络：需能访问新浪财经公开接口；限速约每请求间隔 `REQUEST_SLEEP_SEC`（见 `config.py`）。  
- **非投资建议**；公开接口字段可能变更；配股等复杂除权首版可能忽略（见台账 §7.2.1）。  
- 与缠论/中枢信号交叉：**首版不做**。

---

## 7. 快速检查清单

1. 在仓库根目录打开终端。  
2. 跑 `--codes` 烟雾或 `--pool`。  
3. 打开 `outputs/` 下 CSV，看 `M_gate` 与 `X_of_4`。  
4. 需要新数据：加 `--no-cache` 或清 `cache/` 后再跑。
