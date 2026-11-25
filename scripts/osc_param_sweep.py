#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：CLI 日期区间、网格步长、--models、--resume、--use-features 等
# 输出参数：outputs/param_sweep/osc_grid_*/ 各组 trades、summary
# 用途：震荡开仓阈值网格扫参；进程内共享 FeatureStore
# 使用说明：python scripts/osc_param_sweep.py [--resume] [--rebuild-features]

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.data_portal import DataPortal
from run_backtest import run_backtest
from strategy.backtest_params import default_strategy_params
from strategy.feature_store import resolve_feature_store

NEAR_START = 0.002
NEAR_END = 0.015
NEAR_STEP = 0.003
BREAK_START = 0.005
BREAK_END = 0.02
BREAK_STEP = 0.005

RESULT_FIELDS = [
    'model',
    'near_pct',
    'break_pct',
    'near_decimal',
    'break_decimal',
    'return_pct',
    'total_return',
    'dd1_pct',
    'dd2_pct',
    'dd3_pct',
    'open_count',
    'close_count',
    'run_name',
    'output_dir',
    'status',
    'error',
]


def _frange(start: float, end: float, step: float) -> list[float]:
    vals: list[float] = []
    x = start
    # 避免浮点累积误差
    n = int(round((end - start) / step)) + 1
    for i in range(n):
        vals.append(round(start + i * step, 6))
    return vals


def build_grid() -> list[tuple[float, float]]:
    near_vals = _frange(NEAR_START, NEAR_END, NEAR_STEP)
    break_vals = _frange(BREAK_START, BREAK_END, BREAK_STEP)
    return [(n, b) for n in near_vals for b in break_vals]


def pct_label(v: float) -> str:
    return f'{v * 100:.1f}'


def run_name_for(model: str, near: float, break_: float) -> str:
    def _tag(x: float) -> str:
        s = f'{x * 100:.1f}'.replace('.', 'p').replace('-', 'm')
        return s

    return f'sweep_{model}_n{_tag(near)}_b{_tag(break_)}'


def load_daily_equity(equity_csv: Path) -> list[float]:
    df = pd.read_csv(equity_csv, encoding='utf-8-sig')
    dt_col = '日期时间' if '日期时间' in df.columns else 'datetime'
    eq_col = '总权益' if '总权益' in df.columns else 'total_value'
    if dt_col not in df.columns or eq_col not in df.columns:
        raise ValueError(f'equity.csv 缺少列: {equity_csv}')
    df = df.copy()
    df['_dt'] = pd.to_datetime(df[dt_col], errors='coerce')
    df = df.dropna(subset=['_dt', eq_col])
    df['_d'] = df['_dt'].dt.date
    daily = df.groupby('_d', sort=True)[eq_col].last()
    return [float(x) for x in daily.tolist()]


def top_n_drawdown_pcts(equity_series: list[float], n: int = 3) -> list[float]:
    """独立峰值—谷底回撤段，返回正数百分比（如 45.2 表示 45.2%）。"""
    if not equity_series:
        return [0.0] * n

    episodes: list[float] = []
    peak = float(equity_series[0])
    trough = peak
    in_dd = False

    for eq in equity_series:
        v = float(eq)
        if v >= peak:
            if in_dd and peak > 0:
                depth = (peak - trough) / peak
                if depth > 1e-12:
                    episodes.append(depth * 100.0)
            peak = v
            trough = v
            in_dd = False
        else:
            in_dd = True
            trough = min(trough, v)

    if in_dd and peak > 0:
        depth = (peak - trough) / peak
        if depth > 1e-12:
            episodes.append(depth * 100.0)

    episodes.sort(reverse=True)
    while len(episodes) < n:
        episodes.append(0.0)
    return episodes[:n]


def analyze_run(output_dir: Path) -> dict:
    summary_path = output_dir / 'summary.json'
    equity_path = output_dir / 'equity.csv'
    if not summary_path.exists():
        raise FileNotFoundError(f'缺少 summary.json: {output_dir}')
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    total_return = float(summary.get('total_return', 0.0) or 0.0)
    dd = [0.0, 0.0, 0.0]
    if equity_path.exists():
        daily_eq = load_daily_equity(equity_path)
        dd = top_n_drawdown_pcts(daily_eq, 3)
    return {
        'total_return': total_return,
        'return_pct': total_return * 100.0,
        'dd1_pct': dd[0],
        'dd2_pct': dd[1],
        'dd3_pct': dd[2],
        'open_count': int(summary.get('open_count', 0) or 0),
        'close_count': int(summary.get('close_count', 0) or 0),
    }


def load_existing_keys(results_csv: Path) -> set[tuple[str, float, float]]:
    if not results_csv.exists():
        return set()
    df = pd.read_csv(results_csv)
    keys: set[tuple[str, float, float]] = set()
    for _, row in df.iterrows():
        if str(row.get('status', '')).lower() != 'ok':
            continue
        keys.add((str(row['model']), float(row['near_decimal']), float(row['break_decimal'])))
    return keys


def append_result(results_csv: Path, row: dict) -> None:
    results_csv.parent.mkdir(parents=True, exist_ok=True)
    write_header = not results_csv.exists()
    with results_csv.open('a', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS, extrasaction='ignore')
        if write_header:
            w.writeheader()
        w.writerow(row)


def run_one_backtest(
    *,
    model: str,
    near: float,
    break_: float,
    start_date: str,
    end_date: str,
    runs_dir: Path,
    initial_cash: float,
    feature_store,
    data_dir: Path,
    use_features: bool,
) -> Path:
    name = run_name_for(model, near, break_)
    result = run_backtest(
        start_date,
        end_date,
        data_dir=data_dir,
        output_dir=runs_dir,
        run_name=name,
        initial_cash=initial_cash,
        sync_data=False,
        viz=False,
        use_features=use_features,
        feature_store=feature_store if use_features else None,
        strategy_param_strings=[
            f'OSC_RULES_MODEL={model}',
            f'OSC_NEAR_REF_THRESHOLD={near}',
            f'OSC_BREAK_REF_THRESHOLD={break_}',
        ],
    )
    out_dir = Path(result['output_dir'])
    if not (out_dir / 'summary.json').exists():
        raise FileNotFoundError(f'未找到回测输出: {name}')
    return out_dir


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='震荡阈值网格回测 v1/v2')
    p.add_argument('--start-date', default='2016-01-01')
    p.add_argument('--end-date', default='2026-05-31')
    p.add_argument('--models', default='v1,v2', help='逗号分隔，如 v1,v2')
    p.add_argument('--output-dir', default=None, help='默认 outputs/param_sweep/osc_grid_<时间戳>')
    p.add_argument('--initial-cash', type=float, default=10_000_000.0)
    p.add_argument('--resume', action='store_true', help='跳过 sweep_results.csv 中已成功的组合')
    p.add_argument('--dry-run', action='store_true', help='只打印网格，不回测')
    p.add_argument('--limit', type=int, default=0, help='最多跑 N 组（0=全部）')
    p.add_argument('--data-dir', default=None, help='行情目录，默认项目 data/')
    p.add_argument('--features-root', default=None, help='特征表根目录，默认 data/features')
    p.add_argument('--rebuild-features', action='store_true', help='扫参开始前强制新建特征表')
    feat = p.add_mutually_exclusive_group()
    feat.add_argument('--use-features', dest='use_features', action='store_true', help='共享预计算特征表（默认）')
    feat.add_argument('--no-features', dest='use_features', action='store_false', help='禁用特征表')
    p.set_defaults(use_features=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    models = [m.strip().lower() for m in args.models.split(',') if m.strip()]
    for m in models:
        if m not in ('v1', 'v2'):
            print(f'未知模型: {m}', file=sys.stderr)
            return 2

    grid = build_grid()
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    sweep_dir = Path(args.output_dir) if args.output_dir else (ROOT / 'outputs' / 'param_sweep' / f'osc_grid_{ts}')
    runs_dir = sweep_dir / 'runs'
    runs_dir.mkdir(parents=True, exist_ok=True)
    results_csv = sweep_dir / 'sweep_results.csv'
    config_path = sweep_dir / 'sweep_config.json'

    data_dir = Path(args.data_dir) if args.data_dir else (ROOT / 'data')
    feature_store = None
    feature_bundle_id = None
    feature_store_dir = None

    config = {
        'start_date': args.start_date,
        'end_date': args.end_date,
        'models': models,
        'near_grid': {'start_pct': NEAR_START * 100, 'end_pct': NEAR_END * 100, 'step_pct': NEAR_STEP * 100},
        'break_grid': {'start_pct': BREAK_START * 100, 'end_pct': BREAK_END * 100, 'step_pct': BREAK_STEP * 100},
        'grid_size': len(grid),
        'total_jobs': len(grid) * len(models),
        'initial_cash': args.initial_cash,
        'drawdown_method': 'top3_independent_peak_trough_episodes_daily_equity',
        'use_features': args.use_features,
    }

    jobs: list[tuple[str, float, float]] = []
    for model in models:
        for near, break_ in grid:
            jobs.append((model, near, break_))

    if args.limit and args.limit > 0:
        jobs = jobs[: args.limit]

    existing = load_existing_keys(results_csv) if args.resume else set()

    print(f'网格: 贴近 {len(_frange(NEAR_START, NEAR_END, NEAR_STEP))} × 突破 {len(_frange(BREAK_START, BREAK_END, BREAK_STEP))} = {len(grid)}')
    print(f'模型: {models} | 任务数: {len(jobs)} | 输出: {sweep_dir}')

    if args.dry_run:
        for i, (model, near, break_) in enumerate(jobs, 1):
            print(f'{i:3d} {model} near={pct_label(near)}% break={pct_label(break_)}% -> {run_name_for(model, near, break_)}')
        return 0

    if args.use_features:
        portal = DataPortal(str(data_dir))
        start = pd.to_datetime(args.start_date).normalize()
        end = pd.to_datetime(args.end_date).normalize()
        trade_dates = [
            pd.Timestamp(d).date()
            for d in portal.get_trade_dates('上证指数30分钟加入实时数据.csv')
            if start <= d <= end
        ]
        if not trade_dates:
            print('指定区间内无交易日', file=sys.stderr)
            return 2
        base_cfg = SimpleNamespace(**default_strategy_params())
        print(
            f'预构建/加载 FeatureStore: {len(trade_dates)} 交易日 '
            f'({args.start_date} ~ {args.end_date}) ...',
            flush=True,
        )
        feature_store = resolve_feature_store(
            portal,
            base_cfg,
            trade_dates,
            features_root=args.features_root,
            rebuild=args.rebuild_features,
        )
        feature_bundle_id = feature_store.bundle_id
        feature_store_dir = str(feature_store.store_dir)
        config['feature_bundle_id'] = feature_bundle_id
        config['feature_store_dir'] = feature_store_dir
        print(f'FeatureStore: bundle_id={feature_bundle_id}', flush=True)
        print(f'FeatureStore: dir={feature_store_dir}', flush=True)

    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

    ok, fail, skip = 0, 0, 0

    for i, (model, near, break_) in enumerate(jobs, 1):
        key = (model, near, break_)
        if key in existing:
            skip += 1
            print(f'[{i}/{len(jobs)}] 跳过 {model} near={pct_label(near)}% break={pct_label(break_)}%')
            continue

        print(f'[{i}/{len(jobs)}] 回测 {model} near={pct_label(near)}% break={pct_label(break_)}% ...', flush=True)
        row = {
            'model': model,
            'near_pct': pct_label(near),
            'break_pct': pct_label(break_),
            'near_decimal': near,
            'break_decimal': break_,
            'status': 'error',
            'error': '',
            'run_name': run_name_for(model, near, break_),
            'output_dir': '',
            'return_pct': '',
            'total_return': '',
            'dd1_pct': '',
            'dd2_pct': '',
            'dd3_pct': '',
            'open_count': '',
            'close_count': '',
        }
        try:
            out_dir = run_one_backtest(
                model=model,
                near=near,
                break_=break_,
                start_date=args.start_date,
                end_date=args.end_date,
                runs_dir=runs_dir,
                initial_cash=args.initial_cash,
                feature_store=feature_store,
                data_dir=data_dir,
                use_features=args.use_features,
            )
            metrics = analyze_run(out_dir)
            row.update(metrics)
            row['run_name'] = out_dir.name
            row['output_dir'] = str(out_dir)
            row['status'] = 'ok'
            ok += 1
            print(
                f'  -> 收益 {metrics["return_pct"]:.2f}% | '
                f'回撤 {metrics["dd1_pct"]:.2f}% / {metrics["dd2_pct"]:.2f}% / {metrics["dd3_pct"]:.2f}%',
                flush=True,
            )
        except Exception as e:
            row['error'] = str(e)[:500]
            fail += 1
            print(f'  !! 失败: {e}', flush=True)

        append_result(results_csv, row)

    print(f'\n完成: 成功 {ok} | 失败 {fail} | 跳过 {skip}')
    print(f'结果表: {results_csv}')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
