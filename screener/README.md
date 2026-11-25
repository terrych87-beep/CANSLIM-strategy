# screener — 选股子系统

与中枢 MACD / IF 策略隔离的选股工具目录。

## 文档

| 文档 | 内容 |
|------|------|
| [CANSLIM_使用说明.md](./CANSLIM_使用说明.md) / [.pdf](./CANSLIM_使用说明.pdf) | 选股/评分思路、怎么跑、看哪张表、是否自动更新、如何手动刷新 |
| [CANSLIM_模块需求说明书.md](./CANSLIM_模块需求说明书.md) / [.pdf](./CANSLIM_模块需求说明书.pdf) | 各模块功能与端到端串联 |
| [CANSLIM_全池评分明细.xlsx](./CANSLIM_全池评分明细.xlsx) | 1067 只股票评分指标与明细（多 Sheet） |

## 代码

- `canslim/` — 欧奈尔 CANSLIM 实现（新浪取数 + 自建前复权）
