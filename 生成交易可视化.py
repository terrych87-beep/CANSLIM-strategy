#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：CLI run_output_dir [data_dir]
# 输出参数：调用 trade_visualizer 生成 ECharts HTML
# 用途：兼容入口；优先使用 run_backtest --viz 或 tools/trade_visualizer.py
# 使用说明：python 生成交易可视化.py outputs/backtest/<run_name>

from pathlib import Path

from tools.trade_visualizer import render_backtest_visuals

if __name__ == '__main__':
    import sys

    root = Path(__file__).resolve().parent
    if len(sys.argv) < 2:
        raise SystemExit('用法: python 生成交易可视化.py <run_output_dir> [data_dir]')
    run_dir = Path(sys.argv[1])
    data_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else root / 'data'
    out = render_backtest_visuals(run_dir, data_dir)
    print(out)
