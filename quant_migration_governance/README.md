# quant_migration_governance

本目录**仅维护**各版本 **`BASELINE*.md`** 与 Git 标签成对使用。

历史曾规划 `ACCEPTANCE_CHECKLIST.md`、`MACD_LOCAL_CALIBRATION.md`、`RUNPOINT_FLOW.md` 等独立文档，**不再单独维护**；请以当前基线为准：

| 主题 | 权威来源 |
|------|----------|
| 版本变更、回归清单 | **`BASELINE_v3.1.md`**（当前） |
| MACD 本地口径（B 类） | **`.cursor/rules/local-macd-reconciliation-baseline.mdc`** + `strategy/macd_divergence.py` → `MacdDivCfg` |
| 运行点顺序 | `engine/event_engine.py` → `RUNPOINTS` |
| 变更流水 | 根目录 `CHANGELOG.md` |

```powershell
git checkout v3.1.0-baseline-20260624
```
