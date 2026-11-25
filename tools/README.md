# tools/

与主链回测并行、但**非** `run_backtest.py` 核心路径的辅助脚本。

| 脚本 | 用途 |
|------|------|
| `trade_visualizer.py` | 回测成交 ECharts 可视化（供 `生成交易可视化.py` / `run_backtest --viz` 调用） |
| `大盘指数爬取.py` | 股宝网 DDX：拉取上证 BBD 日线/分时，写入根目录 `大盘资金流向.xlsx` |

## 大盘 BBD 爬取（sync）

**推荐命令**（日线 + 分时增量 + 可选校验）：

```powershell
.\env\run_with_conda.bat tools\大盘指数爬取.py
.\env\run_with_conda.bat tools\大盘指数爬取.py --mode sync --verify
```

| 输出 | 路径 |
|------|------|
| 正式 Excel | `大盘资金流向.xlsx`（项目根目录） |
| Sheet `散点数据` | API 原始累计 BBD（与 `大盘指数模板.xlsx` Sheet1 同格式） |
| Sheet `分时数据` | 5 分钟非累计 BBD（9:35~11:30、13:05~15:00） |
| Sheet `日线数据` | 日线 BBD（自原 `daily数据` 复制/增量） |
| 散点历史导入源 | `大盘指数模板.xlsx` Sheet1（≤2025-12-05） |
| 增量状态 | `大盘资金流向_lastfilemtime.txt`（与 Excel 同目录） |
| 校验参照（只读） | `data/大盘指数模板.xlsx` |

### 行为摘要

- **`--mode sync`（默认）**：日线双轮补洞至今日；散点按 A 股历补 `2025-12-05` 之后缺失日；由散点转为 **5 分钟段内非累计** 写入 `分时数据`；今日每次覆盖重拉。
- **`--mode rebuild`**：日线 sync → 从 `大盘指数模板.xlsx` 导入历史散点 → 清除 floor 之后错误散点 → API 全量回填 → 重建全部 5 分钟分时。
- **API 日期格式**：分时接口 `date` 必须为 **`YYYY-MM-DD`**（如 `2026-06-23`）；`YYYYMMDD` 会返回最新一天假数据。
- **5 分钟换算**：首根 9:35 用 `cum(9:35)`；其后为段内非累计（缺点按比例插值，最多向前 10 分钟）；BBD **单位万元**，`|段增量| < 10` 置空。
- **`--verify`**：日线与 `data/大盘指数模板.xlsx` 严格对照；样本日 5 分钟分时与 API 重算对照；与参照散点差异仅日志提示。有**阻断性**差异则 `exit≠0`。
- 收盘前运行：允许今日分时不完整，日志提示「待收盘后重跑」。

### 手工调试

```powershell
.\env\run_with_conda.bat tools\大盘指数爬取.py --mode rebuild
.\env\run_with_conda.bat tools\大盘指数爬取.py --mode rebuild --verify
.\env\run_with_conda.bat tools\大盘指数爬取.py --mode daily
.\env\run_with_conda.bat tools\大盘指数爬取.py --mode intraday --date 20260624
.\env\run_with_conda.bat tools\大盘指数爬取.py --mode both --full
```
