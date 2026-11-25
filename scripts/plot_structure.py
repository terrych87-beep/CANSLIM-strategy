#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：CLI --start-date、--end-date；可选 --output、结构参数
# 输出参数：outputs/structure/structure_*.html（ECharts）
# 用途：CLI 生成结构图；与 9:00 回测口径分离（viz_full_history）
# 使用说明：python scripts/plot_structure.py --start-date 2014-01-01 --end-date 2026-06-05

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy.backtest_params import default_strategy_params, parse_strategy_param_strings
from strategy.feature_store import _struct_cfg

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from structure_viz_core import run_structure_plot


def parse_args():
    p = argparse.ArgumentParser(description='日线中枢/range/笔 结构可视化（HTML）')
    p.add_argument('--start-date', required=True)
    p.add_argument('--end-date', required=True)
    p.add_argument('--data-dir', default=None)
    p.add_argument('--daily-file', default='上证指数日线加入实时数据.csv')
    p.add_argument('--output', default=None)
    p.add_argument('--strategy-param', action='append', default=[], metavar='KEY=VALUE')
    return p.parse_args()


def main():
    args = parse_args()
    data_dir = Path(args.data_dir) if args.data_dir else ROOT / 'data'
    sp = parse_strategy_param_strings(args.strategy_param) if args.strategy_param else default_strategy_params()
    struct_cfg = _struct_cfg(SimpleNamespace(**sp))

    result = run_structure_plot(
        start_date=args.start_date,
        end_date=args.end_date,
        data_dir=data_dir,
        struct_cfg=struct_cfg,
        daily_file=args.daily_file,
        cache_root=ROOT / 'outputs' / 'structure' / 'cache',
        html_root=ROOT / 'outputs' / 'structure',
        output=Path(args.output) if args.output else None,
    )
    print(f'output={result["output"]}')
    print(
        f'缓存 bundle={result["viz_bundle_id"]} 已缓存 {result["cache_trade_days"]} 日 '
        f'（本次新算 {result["computed_new"]} 日）'
    )
    print(
        f'中枢片段={result["center_segments"]} 区间片段={result["range_segments"]} '
        f'确认笔点={result["solid_points"]} 临时笔点={result["dashed_points"]}'
    )


if __name__ == '__main__':
    main()
