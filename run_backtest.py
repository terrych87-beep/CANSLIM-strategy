#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：start_date、end_date、data_dir、策略参数、initial_cash、leverage、sync/features 开关等
# 输出参数：dict（summary、output_dir、trades_csv、feature_store 路径）；outputs/backtest/{run_name}/ 产物
# 中文映射：
#   - run_name -> 自动命名输出子目录
#   - sync-data / --force-sync-data -> 调用 live_data_sync
#   - --rebuild-features -> FEATURE_SIGNAL_VERSION 变更后重建 parquet
# 用途：统一回测入口：覆盖检查 → 可选 sync → 特征表 resolve → 事件引擎驱动策略
# 使用说明：
#   python run_backtest.py --start-date 2021-01-01 --end-date 2022-01-01
#   网页调试经 网页运行调试策略.py 子进程调用

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from engine.data_coverage import data_covers_backtest_range
from engine.data_portal import DataPortal
from engine.event_engine import EventEngine
from engine.report import ReportWriter
from strategy.backtest_params import default_strategy_params, parse_strategy_param_strings, parse_strategy_params_from_mapping
from strategy.center_macd_strategy import CenterMacdStrategy
from strategy.feature_store import FeatureStore, resolve_feature_store
from paths import prefer_local_drive_path


# make_backtest_run_name 方法说明
# 输入参数：start_date、end_date、total_return；可选 run_time
# 输出参数：str，如 20260605_114530_20160101-20260605_R+10.34pct
# 用途：自动生成回测输出子目录名
def make_backtest_run_name(
    start_date: str,
    end_date: str,
    total_return: float,
    *,
    run_time: datetime | None = None,
) -> str:
    """运行时间 + 回放区间 + 收益率，例如 20260605_114530_20160101-20260605_R+10.34pct"""
    ts = (run_time or datetime.now()).strftime('%Y%m%d_%H%M%S')
    s = pd.to_datetime(start_date).strftime('%Y%m%d')
    e = pd.to_datetime(end_date).strftime('%Y%m%d')
    ret_pct = float(total_return or 0.0) * 100.0
    sign = '+' if ret_pct >= 0 else ''
    return f'{ts}_{s}-{e}_R{sign}{ret_pct:.2f}pct'


def _unique_run_dir(base_output_dir: Path, name: str) -> Path:
    target = base_output_dir / name
    if not target.exists():
        return target
    n = 2
    while (base_output_dir / f'{name}_{n}').exists():
        n += 1
    return base_output_dir / f'{name}_{n}'


# parse_args 方法说明
# 输入参数：无（读取 sys.argv）
# 输出参数：argparse.Namespace（日期、路径、sync/viz/features 开关、strategy-param 列表）
# 用途：CLI 参数解析
def parse_args():
    parser = argparse.ArgumentParser(description='本地量化策略回测入口')
    parser.add_argument('--start-date', required=True)
    parser.add_argument('--end-date', required=True)
    parser.add_argument('--data-dir', default=None)
    parser.add_argument('--output-dir', default=None)
    parser.add_argument('--run-name', default=None, help='可选：指定本次回测子目录名')
    parser.add_argument('--initial-cash', type=float, default=10_000_000.0, help='初始资金')
    parser.add_argument('--leverage', type=float, default=4.0, help='最大名义仓位倍数(MAX_POSITION_RATIO)')
    parser.add_argument('--env-file', default=None, help='实时数据 .env，默认 data/.env')
    sync = parser.add_mutually_exclusive_group()
    sync.add_argument('--sync-data', dest='sync_data', action='store_true', help='回测前同步上证行情（默认开启）')
    sync.add_argument('--no-sync-data', dest='sync_data', action='store_false', help='不同步，使用本地已有 CSV')
    parser.set_defaults(sync_data=True)
    parser.add_argument(
        '--force-sync-data',
        action='store_true',
        help='强制增量同步（即使本地数据已覆盖回测区间）',
    )
    viz = parser.add_mutually_exclusive_group()
    viz.add_argument('--viz', dest='viz', action='store_true', help='回测后生成可视化 HTML（默认开启）')
    viz.add_argument('--no-viz', dest='viz', action='store_false', help='不生成可视化')
    parser.set_defaults(viz=True)
    parser.add_argument('--viz-initial-asset', type=float, default=None, help='可视化收益图初始资金，默认同 --initial-cash')
    parser.add_argument(
        '--strategy-param',
        action='append',
        default=[],
        metavar='KEY=VALUE',
        help='策略参数，可重复，如 --strategy-param OSC_NEAR_REF_THRESHOLD=0.005',
    )
    feat = parser.add_mutually_exclusive_group()
    feat.add_argument('--use-features', dest='use_features', action='store_true', help='使用预计算特征表（默认开启）')
    feat.add_argument('--no-features', dest='use_features', action='store_false', help='禁用特征表，运行时逐点计算')
    parser.set_defaults(use_features=True)
    parser.add_argument('--features-root', default=None, help='特征表根目录，默认 data/features')
    parser.add_argument('--rebuild-features', action='store_true', help='忽略已有表，按当前 signal_version 与数据新建特征表（14:57 break_up 变更后必用）')
    return parser.parse_args()


# run_backtest 方法说明
# 输入参数：start_date、end_date；可选 data_dir、output_dir、策略参数、sync_data、use_features 等
# 输出参数：dict（回测摘要、输出路径、成交统计、特征表 bundle_id、sync_warnings）
# 用途：执行完整回测流程并落盘报告
def run_backtest(
    start_date: str,
    end_date: str,
    *,
    data_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    run_name: str | None = None,
    initial_cash: float = 10_000_000.0,
    leverage: float = 4.0,
    sync_data: bool = True,
    force_sync_data: bool = False,
    env_file: Path | str | None = None,
    viz: bool = True,
    viz_initial_asset: float | None = None,
    strategy_params: dict | None = None,
    strategy_param_strings: list[str] | None = None,
    use_features: bool = True,
    features_root: Path | str | None = None,
    rebuild_features: bool = False,
    feature_store: FeatureStore | None = None,
) -> dict:
    root = prefer_local_drive_path(Path(__file__).resolve().parent)
    data_dir = Path(data_dir) if data_dir else (root / 'data')
    base_output_dir = Path(output_dir) if output_dir else (root / 'outputs' / 'backtest')
    base_output_dir.mkdir(parents=True, exist_ok=True)
    run_started = datetime.now()
    user_run_name = (run_name or '').strip() or None
    if user_run_name:
        output_run_dir = _unique_run_dir(base_output_dir, user_run_name)
        run_name = output_run_dir.name
    else:
        pending = f'{run_started.strftime("%Y%m%d_%H%M%S")}_pending'
        output_run_dir = _unique_run_dir(base_output_dir, pending)
        run_name = output_run_dir.name
    viz_asset = float(viz_initial_asset if viz_initial_asset is not None else initial_cash)
    sync_warnings: list[str] = []
    sync_skipped = False

    data_portal = DataPortal(str(data_dir))
    start = pd.to_datetime(start_date).normalize()
    end = pd.to_datetime(end_date).normalize()

    if sync_data:
        covered, cover_msg = data_covers_backtest_range(data_portal, start, end)
        if covered and not force_sync_data:
            sync_skipped = True
            msg = f'[sync-data] 跳过：{cover_msg}'
            print(msg)
            sync_warnings.append(msg)
        else:
            from scripts.live_data_sync import sync_live_data

            if not covered:
                print(f'[sync-data] 增量同步：{cover_msg}')
            else:
                print('[sync-data] 强制增量同步（--force-sync-data）')
            sync_ret = sync_live_data(
                root,
                data_dir=data_dir,
                env_file=env_file,
                fail_soft=True,
                start_date=start_date,
                end_date=end_date,
                force=force_sync_data,
            )
            sync_warnings.extend(sync_ret.get('warnings') or [])
            if not sync_ret.get('ok'):
                print('[warn] 数据同步未成功，将使用本地已有 CSV 继续回测')
            data_portal = DataPortal(str(data_dir))

    if strategy_params is not None:
        sp = parse_strategy_params_from_mapping(strategy_params)
    elif strategy_param_strings:
        sp = parse_strategy_param_strings(strategy_param_strings)
    else:
        sp = default_strategy_params()
    config = SimpleNamespace(MAX_POSITION_RATIO=float(leverage), **sp)
    trade_dates = [
        d for d in data_portal.get_trade_dates('上证指数30分钟加入实时数据.csv')
        if start <= d <= end
    ]
    feature_store_dir = None
    if use_features:
        trade_dates_for_feat = [pd.Timestamp(d).date() for d in trade_dates]
        if feature_store is not None:
            if not feature_store.covers_dates(trade_dates_for_feat):
                raise ValueError(
                    f'共享 FeatureStore({feature_store.bundle_id}) 未覆盖回测区间全部交易日，'
                    f'请扩大预构建范围或去掉 feature_store 参数'
                )
            feature_store_dir = str(feature_store.store_dir)
        else:
            feature_store = resolve_feature_store(
                data_portal,
                config,
                trade_dates_for_feat,
                features_root=features_root,
                rebuild=rebuild_features,
            )
            feature_store_dir = str(feature_store.store_dir)
    else:
        feature_store = None
    strategy = CenterMacdStrategy(
        data_portal, config=config, initial_cash=float(initial_cash), feature_store=feature_store,
    )
    strategy.initialize()
    engine = EventEngine()
    report = ReportWriter(output_run_dir)

    for td in trade_dates:
        for event in engine.build_daily_events(pd.Timestamp(td).date()):
            if event.event_type == 'before_market_open':
                strategy.before_market_open(event)
            elif event.event_type == 'roll_contract':
                strategy.roll_contract_if_needed(event)
            elif event.event_type == 'after_market_close':
                strategy.after_market_close(event)
            else:
                strategy.handle_run_point(event)

    bundle = {
        'run_log': strategy.run_log,
        'trades': strategy.trades,
        'equity': strategy.equity,
        'state_snapshots': strategy.state_snapshots,
        'summary': strategy.summary,
    }
    report.write_bundle(bundle)

    total_return = float((strategy.summary or {}).get('total_return', 0.0) or 0.0)
    if not user_run_name:
        final_name = make_backtest_run_name(start_date, end_date, total_return, run_time=run_started)
        final_dir = _unique_run_dir(base_output_dir, final_name)
        if final_dir != output_run_dir:
            output_run_dir.rename(final_dir)
            output_run_dir = final_dir
        run_name = output_run_dir.name

    viz_paths = {}
    if viz:
        from tools.trade_visualizer import render_backtest_visuals

        viz_paths = render_backtest_visuals(output_run_dir, data_dir, initial_asset=viz_asset)

    result = {
        'project_root': str(root),
        'start_date': start_date,
        'end_date': end_date,
        'trade_days': len(trade_dates),
        'events_per_day': len(engine.runpoints),
        'output_dir': str(prefer_local_drive_path(output_run_dir)),
        'run_name': run_name,
        'strategy_params': sp,
        'feature_store': feature_store_dir,
        'feature_bundle_id': feature_store.bundle_id if feature_store else None,
        'summary': strategy.summary,
        'sync_warnings': sync_warnings,
        'sync_skipped': sync_skipped,
        'viz': viz_paths,
        'trades_csv': str(output_run_dir / 'trades.csv'),
        'summary_json': str(output_run_dir / 'summary.json'),
    }
    meta_path = output_run_dir / 'run_meta.json'
    meta_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    result['backtest_output_dir'] = str(prefer_local_drive_path(output_run_dir))
    return result


# main 方法说明
# 输入参数：无（CLI）
# 输出参数：无（打印 run_meta 摘要到 stdout）
# 用途：命令行入口
def main():
    args = parse_args()
    root = prefer_local_drive_path(Path(__file__).resolve().parent)
    data_dir = args.data_dir or str(root / 'data')
    output_dir = args.output_dir or str(root / 'outputs' / 'backtest')
    env_file = args.env_file or str(Path(data_dir) / '.env')
    result = run_backtest(
        args.start_date,
        args.end_date,
        data_dir=data_dir,
        output_dir=output_dir,
        run_name=args.run_name,
        initial_cash=args.initial_cash,
        leverage=args.leverage,
        sync_data=args.sync_data,
        force_sync_data=args.force_sync_data,
        env_file=env_file,
        viz=args.viz,
        viz_initial_asset=args.viz_initial_asset,
        strategy_param_strings=args.strategy_param or [],
        use_features=args.use_features,
        features_root=args.features_root,
        rebuild_features=args.rebuild_features,
    )
    print(f"project_root={result['project_root']}")
    print(f"start={result['start_date']}, end={result['end_date']}, trade_days={result['trade_days']}, events_per_day={result['events_per_day']}")
    print(f"output_dir={result['output_dir']}")
    print(f"backtest_output_dir={result.get('backtest_output_dir', result['output_dir'])}")
    if result.get('sync_warnings'):
        print('sync_warnings:')
        for w in result['sync_warnings']:
            print(f'  - {w}')
    if result.get('viz'):
        print(f"trade_chart={result['viz'].get('trade_chart')}")
        print(f"equity_chart={result['viz'].get('equity_chart')}")


if __name__ == '__main__':
    main()
