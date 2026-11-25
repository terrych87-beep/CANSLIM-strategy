#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：CLI --start-date、--end-date；可选 --data-dir、--strategy-param、--rebuild
# 输出参数：data/features/{bundle_id}/ 下 manifest + parquet
# 中文映射：
#   - bundle_id -> 结构/MACD 参数指纹目录
#   - structure_daily / signals_runpoint -> 预计算 parquet 表
# 用途：回测前批量预计算特征，加速长区间与扫参
# 使用说明：python scripts/build_features.py --start-date 2020-01-01 --end-date 2025-12-31

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.data_portal import DataPortal
from strategy.backtest_params import default_strategy_params, parse_strategy_param_strings
from strategy.feature_store import resolve_feature_store
from types import SimpleNamespace


def parse_args():
    p = argparse.ArgumentParser(description='预构建中枢/MACD 特征表（parquet）')
    p.add_argument('--start-date', required=True)
    p.add_argument('--end-date', required=True)
    p.add_argument('--data-dir', default=None)
    p.add_argument('--features-root', default=None)
    p.add_argument('--rebuild', action='store_true', help='强制新建特征表目录')
    p.add_argument(
        '--strategy-param',
        action='append',
        default=[],
        metavar='KEY=VALUE',
        help='仅 StructCfg/MacdDivCfg 相关参数会影响表身份',
    )
    return p.parse_args()


def main():
    args = parse_args()
    data_dir = Path(args.data_dir) if args.data_dir else ROOT / 'data'
    portal = DataPortal(str(data_dir))
    sp = parse_strategy_param_strings(args.strategy_param) if args.strategy_param else default_strategy_params()
    cfg = SimpleNamespace(**sp)

    start = pd.to_datetime(args.start_date).normalize()
    end = pd.to_datetime(args.end_date).normalize()
    trade_dates = [
        pd.Timestamp(d).date()
        for d in portal.get_trade_dates('上证指数30分钟加入实时数据.csv')
        if start <= d <= end
    ]
    if not trade_dates:
        raise SystemExit('指定区间内无交易日')

    store = resolve_feature_store(
        portal,
        cfg,
        trade_dates,
        features_root=args.features_root,
        rebuild=args.rebuild,
    )
    print(f'bundle_id={store.bundle_id}')
    print(f'store_dir={store.store_dir}')
    print(f'trade_days={len(trade_dates)}')
    manifest_path = store.store_dir / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        print(f"struct_cfg={json.dumps(manifest['identity']['struct_cfg'], ensure_ascii=False)}")
        print(f"macd_cfg={json.dumps(manifest['identity']['macd_cfg'], ensure_ascii=False)}")


if __name__ == '__main__':
    main()
