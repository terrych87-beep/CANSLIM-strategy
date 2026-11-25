# scripts/

| 脚本 | 类型 | 说明 |
|------|------|------|
| `live_data_sync.py` | **生产** | 封装 `data/实时数据运行.py`；run_backtest 调用 |
| `build_features.py` | **生产** | 预构建特征表 parquet |
| `verify_unclosed_bar_filter.py` | **回归** | v3.1 未收盘 K 线规则自检 |
| `jq_export_kline.py` | 工具 | 聚宽回测环境导出 K 线日志 |
| `parse_jq_kline_log.py` | 工具 | 解析聚宽日志 → CSV |
| `merge_jq_export_1m.py` | 工具 | 合并聚宽 1m 进本地主 CSV |
| `plot_structure.py` | 工具 | CLI 结构可视化 |
| `structure_viz_core.py` | 库 | 结构图渲染核心（网页/CLI 共用） |
| `osc_param_sweep.py` | 研究 | 震荡阈值网格扫参 |
| `analyze_sweep_close_pnl.py` | 研究 | 扫参结果平仓盈亏汇总 |
| `probe_em_sina.py` | 探测 | 东财/新浪/IF 可用性（非回归必跑） |
| `probe_30m_sources.py` | 探测 | 多源 30m 对比 |
| `probe_30m_retry.py` | 探测 | 30m 末段重试对比 |

数据同步主脚本在 **`data/实时数据运行.py`**（非本目录）。
