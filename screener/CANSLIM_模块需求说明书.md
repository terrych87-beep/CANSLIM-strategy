# CANSLIM 选股子系统 — 模块需求说明书

> 目标：把 `screener/canslim` 各模块功能与调用关系写清，便于维护与二次开发。  
> 使用方法见同目录 [`CANSLIM_使用说明.md`](./CANSLIM_使用说明.md)。  
> 业务口径与历史决策见 `docs/plans/CANSLIM_落地步骤.md`。

---

## 1. 系统边界

| 项 | 说明 |
|----|------|
| 输入 | 股票代码列表，或「沪深300∪中证1000」成分 |
| 输出 | 打分 CSV（研究名单） |
| 不做 | 下单、回测撮合、与中枢 `strategy/` / `engine/` / `run_backtest.py` 联动 |
| 数据源 | 新浪公开接口；本地 JSON 缓存 |

**隔离原则**：本子系统可独立运行；禁止为图省事 `import` 中枢 `data/实时数据运行.py`（允许「风格对齐」，代码独立复制精简）。

---

## 2. 端到端串联（主链路）

```
run_screen.py (CLI)
    │
    ├─① 股票池
    │     constituents.fetch_pool_hs300_csi1000()
    │     或 --codes 手工列表
    │
    ├─② 基准日 K（一次）
    │     kline.fetch_daily_kline(sh000300)
    │     factors.m_gate.score_m(bench) → 打印闸门预览
    │
    └─③ 对每只股票 score.score_one(code, bench_kline=...)
            │
            ├─ finance.fetch_finance_gjzb(code)     → 财务宽表
            ├─ kline.fetch_daily_kline(code)        → 不复权日 K
            ├─ bonus.fetch_bonus_events(code)       → 分红事件
            ├─ qfq.apply_qfq(kline, bonus)          → 前复权 OHLC
            │
            ├─ factors.c_a.score_c / score_a        → C、A
            ├─ factors.s_l.score_s / score_l        → S、L（用前复权价/量）
            ├─ factors.m_gate.score_m(bench)        → M（基准，不进 X/4）
            ├─ factors.s_l.near_52w_high            → 辅助列
            │
            └─ 汇总 X_of_4 / known_of_4 / detail / error
                    │
                    └─ run_screen 周期性 flush → outputs/*.csv
```

**数据依赖方向**（箭头表示「被谁使用」）：

```
新浪 HTTP
  → http_util（缓存/UA/代码规范化）
      → constituents / kline / bonus / finance
          → qfq（kline + bonus）
              → score_one
                  → factors（c_a / s_l / m_gate）
                      → run_screen 落盘
```

---

## 3. 目录与模块职责

```
screener/
  CANSLIM_使用说明.md          ← 给人用的操作文档
  CANSLIM_模块需求说明书.md    ← 本文
  canslim/
    config.py                  阈值、基准、限速、路径
    run_screen.py              CLI 入口与批量编排
    score.py                   单票编排（取数→复权→因子→汇总）
    data_sina/                 新浪取数与复权
    factors/                   纯因子计算（尽量无 IO）
    cache/                     本地缓存（gitignore）
```

### 3.1 `config.py`

| 需求 | 说明 |
|------|------|
| 集中配置 | 阈值（C/A/S/L）、基准 `BENCHMARK_SYMBOL`、日 K 根数、量能窗口、MA 参数、HTTP 超时与限速 |
| 路径 | `CACHE_DIR` 指向 `screener/canslim/cache` |
| 约束 | 改阈值须同步更新使用说明与台账；不得静默改回聚宽/东财默认 |

### 3.2 `run_screen.py`（编排层）

| 功能 | 说明 |
|------|------|
| 参数解析 | `--pool` / `--codes` / `--out` / `--limit` / `--resume` / `--no-cache` / `--min-x` / `--flush-every` |
| 建池 | 调 `fetch_pool_hs300_csi1000` 或解析 codes（`zfill(6)`） |
| 基准 | 拉一次沪深300 日 K，算 M 并打印 |
| 循环打分 | 调 `score_one`；支持 resume 跳过已成功代码 |
| 落盘 | `_write_csv`：临时文件替换；`code` 强制六位字符串 |
| 过滤导出 | `--min-x` 时另写 `*_min{N}.csv` |

**验收**：全池可跑通；中断后 `--resume` 不重复成功票；CSV 中 `000001` 类代码前导零保留。

### 3.3 `score.py`（单票用例）

| 步骤 | 模块 | 产出 |
|------|------|------|
| 财务 | `fetch_finance_gjzb` | 多期关键指标 |
| 行情 | `fetch_daily_kline` | 不复权 OHLCV |
| 分红 | `fetch_bonus_events` | 已实施除权事件 |
| 复权 | `apply_qfq` | 前复权 OHLC（量不复权） |
| 因子 | `score_c/a/s/l` + `score_m` | 各字母 pass/value/detail |
| 汇总 | 本模块 | `X_of_4`、辅助列、`error` |

**规则**：任一取数/计算异常 → 捕获后写入 `error`，该票不中断整池。  
**缺数**：因子返回 `pass=None`，不计入通过也不伪造 0。

### 3.4 `data_sina/`（数据层）

| 模块 | 功能 | 上游接口（摘要） | 下游 |
|------|------|------------------|------|
| `http_util.py` | GET、UA、缓存读写、`to_sina_symbol` / `to_six_code` | — | 各 fetch |
| `constituents.py` | 沪深300 JSON 分页；中证1000 HTML 翻页；并集去重 | Market_Center / NewestComponent | run_screen 建池 |
| `kline.py` | 日 K JSONP 解析 | `CN_MarketData.getKLineData` | score、M/L |
| `bonus.py` | 解析分红配送「实施」行 | `vISSUE_ShareBonus` | qfq |
| `finance.py` | 关键财务多期宽表 | `getFinanceReport2022&source=gjzb` | C/A |
| `qfq.py` | 自建前复权 | 无网络 | S/L、52 周高 |
| `symbols.py` | （若存在）代码辅助 | — | 按需 |

#### 前复权需求（`qfq.py`）

- 输入：不复权 K 线 + 分红事件（每 10 股送/转/派息、除权日）。  
- 因子：`factor = (P_pre - cash) / (P_pre × (1 + stk))`，**`P_pre` 必须取自不复权序列**。  
- 顺序：除权日从新到旧；对 `date < ex_date` 的 OHLC 累乘。  
- 窗口：只处理 `first_date < ex_date ≤ last_date`，保证最新价不变。  
- 成交量：首版不复权。  
- 配股：字段不可靠则跳过并记警告（不猜）。

### 3.5 `factors/`（因子层，尽量无 IO）

| 模块 | 函数 | 输入 | 输出要点 |
|------|------|------|----------|
| `c_a.py` | `score_c` | 财务表 | 扣非同比 = 本期/去年同期 − 1 |
| `c_a.py` | `score_a` | 财务表 | 净利同比（优先增长率字段，否则自算）+ ROE |
| `s_l.py` | `score_s` | 前复权 K（用 volume） | 近 20 / 前 60 均量比 |
| `s_l.py` | `score_l` | 个股前复权 + 基准 K | 约 126 根对齐后的超额收益 |
| `s_l.py` | `near_52w_high` | 前复权 K | 辅助比值 |
| `m_gate.py` | `score_m` | 基准 K | green/yellow/red 闸门 |

**串联约束**：S/L 必须吃 **qfq 后**的价格；C/A 只依赖财务，不依赖 K 线；M 只依赖基准指数 K 线。

---

## 4. 配置与外部依赖需求

| 依赖 | 用途 |
|------|------|
| Python 3.x + pandas + requests | 运行时 |
| 新浪可达 | 成分/K线/分红/财务 |
| 写权限 | `outputs/`、`screener/canslim/cache/` |

| 非功能 | 要求 |
|--------|------|
| 限速 | `REQUEST_SLEEP_SEC`，避免封禁 |
| 可恢复 | `--resume` + 定期 flush |
| 可审计 | 每因子 `*_detail`；台账记录口径变更 |
| 安全 | 不存密钥；公开接口仅研究用途 |

---

## 5. 与中枢工程的关系（需求级）

| 中枢侧 | CANSLIM 侧 |
|--------|------------|
| IF / 上证中枢 + MACD 回测 | A 股基本面+量价选股 |
| `data/实时数据运行.py` 增量同步 | 无实时守护；手动 CLI 刷新 |
| `run_backtest.py` | 不调用、不被调用 |

**未来若要「CANSLIM 池 ∩ 缠论信号」**：须另开需求，经确认后再做适配层；不得在未确认时改中枢主干。

---

## 6. 需求追溯（已确认口径 → 模块）

| 口径 | 落地模块 |
|------|----------|
| 池 = 沪深300∪中证1000 | `constituents.py` + `run_screen --pool` |
| 基准 = 沪深300 | `config.BENCHMARK_SYMBOL` + `m_gate` / `score_l` |
| C/A/S/L 阈值 | `config.py` + `factors/*` |
| M 闸门不进分 | `score.py` 汇总逻辑 |
| 新浪主源 | `data_sina/*` |
| 自建前复权 | `bonus.py` + `qfq.py` |
| 缺数≠0 | 各 `score_*` 返回 `pass=None` |
| 隔离中枢 | 独立包路径 `screener/canslim` |

---

## 7. 扩展与变更控制

变更下列任一内容时，须同步改：`config.py`、使用说明、本文、`docs/plans/CANSLIM_落地步骤.md`，并经用户确认后再合入：

- 因子阈值或 X/4 组成  
- 复权公式  
- 主数据源  
- 与中枢/缠论的交叉逻辑  

---

## 8. 测试建议（收口用）

| 级别 | 内容 |
|------|------|
| 烟雾 | `--codes "600519,000858,600000"` 无 error，有 X_of_4 |
| 复权 | 茅台除权日：不复权缺口大、qfq 后接近噪声；最新收盘 raw≈qfq |
| 全池 | `--pool` 行数≈成分并集；抽查 CSV 代码六位 |
| 续跑 | 中断后 `--resume` 行数单调不丢 |
