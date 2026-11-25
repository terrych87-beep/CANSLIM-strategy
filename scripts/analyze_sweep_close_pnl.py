#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：CLI --param-sweep-dir（扫参输出目录）
# 输出参数：stdout 汇总表；可选写出 CSV
# 用途：分析 osc_param_sweep 各组 trades 的平仓盈亏（按持仓类型）
# 使用说明：python scripts/analyze_sweep_close_pnl.py --param-sweep-dir outputs/param_sweep/...

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

OUT_FIELDS = [
    'OSC_NEAR_REF_THRESHOLD',
    'OSC_BREAK_REF_THRESHOLD',
    '持仓类型',
    '合计平仓次数',
    '合计盈亏金额',
    '平均盈亏比例',
]


def parse_pct_ratio(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith('%'):
        s = s[:-1].strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_run_name(name: str) -> tuple[float | None, float | None]:
    """从 sweep_v2_n0p2_b0p5 解析 near/break 小数阈值。"""
    m = re.match(r'sweep_(?:v1|v2)_n(.+)_b(.+?)(?:_\d+)?$', name)
    if not m:
        return None, None

    def _dec(tag: str) -> float:
        neg = tag.startswith('m')
        if neg:
            tag = tag[1:]
        v = float(tag.replace('p', '.'))
        return -v if neg else v

    return _dec(m.group(1)), _dec(m.group(2))


def load_run_index(param_sweep_dir: Path) -> dict[Path, tuple[float, float]]:
    """trades.csv 路径 -> (near_decimal, break_decimal)。"""
    index: dict[Path, tuple[float, float]] = {}
    for results_path in sorted(param_sweep_dir.rglob('sweep_results*.csv')):
        with results_path.open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if str(row.get('status', '')).lower() != 'ok':
                    continue
                out = row.get('output_dir', '').strip()
                if not out:
                    continue
                trades_path = Path(out) / 'trades.csv'
                try:
                    near = float(row['near_decimal'])
                    brk = float(row['break_decimal'])
                except (KeyError, ValueError):
                    continue
                index[trades_path.resolve()] = (near, brk)
    return index


def resolve_params(trades_path: Path, run_index: dict[Path, tuple[float, float]]) -> tuple[float, float] | None:
    key = trades_path.resolve()
    if key in run_index:
        return run_index[key]
    near, brk = parse_run_name(trades_path.parent.name)
    if near is None or brk is None:
        return None
    return near, brk


def analyze_trades(trades_path: Path, near: float, brk: float) -> list[dict]:
    df = pd.read_csv(trades_path, encoding='utf-8-sig')
    if df.empty or '动作' not in df.columns:
        return []

    closes = df[df['动作'].astype(str).str.strip() == '平'].copy()
    if closes.empty:
        return []

    pos_col = '持仓类型'
    if pos_col not in closes.columns:
        return []

    rows: list[dict] = []
    for pos_type, grp in closes.groupby(pos_col, dropna=False):
        pnl = pd.to_numeric(grp.get('盈亏金额'), errors='coerce')
        ratios = grp.get('盈亏比例', pd.Series(dtype=object)).map(parse_pct_ratio)
        valid_ratios = ratios.dropna()
        rows.append(
            {
                'OSC_NEAR_REF_THRESHOLD': near,
                'OSC_BREAK_REF_THRESHOLD': brk,
                '持仓类型': str(pos_type).strip(),
                '合计平仓次数': int(len(grp)),
                '合计盈亏金额': float(pnl.sum(skipna=True)) if pnl.notna().any() else 0.0,
                '平均盈亏比例': float(valid_ratios.mean()) if len(valid_ratios) else None,
            }
        )
    return rows


def format_ratio(v: float | None) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    return f'{v:.2f}%'


def main() -> int:
    p = argparse.ArgumentParser(description='汇总 param_sweep 平仓盈亏（按 OSC 参数 × 持仓类型）')
    p.add_argument(
        '--param-sweep-dir',
        default=str(ROOT / 'outputs' / 'param_sweep'),
        help='扫参根目录或单个 osc_grid_* 子目录',
    )
    p.add_argument(
        '--output',
        default=None,
        help='输出 CSV 路径（默认写在 param_sweep 根目录下）',
    )
    args = p.parse_args()

    sweep_root = Path(args.param_sweep_dir)
    if not sweep_root.exists():
        print(f'目录不存在: {sweep_root}')
        return 1

    # 支持传入 osc_grid_* 单目录或整个 param_sweep
    if sweep_root.name.startswith('osc_grid'):
        search_roots = [sweep_root]
        default_out = sweep_root / 'close_pnl_by_position_type.csv'
    else:
        search_roots = [d for d in sweep_root.iterdir() if d.is_dir() and d.name.startswith('osc_grid')]
        if not search_roots:
            search_roots = [sweep_root]
        default_out = sweep_root / 'close_pnl_by_position_type.csv'

    run_index: dict[Path, tuple[float, float]] = {}
    for root in search_roots:
        run_index.update(load_run_index(root))

    all_rows: list[dict] = []
    seen_runs: set[Path] = set()
    skipped = 0

    for root in search_roots:
        for trades_path in sorted(root.rglob('trades.csv')):
            resolved = trades_path.resolve()
            if resolved in seen_runs:
                continue
            params = resolve_params(trades_path, run_index)
            if params is None:
                skipped += 1
                continue
            seen_runs.add(resolved)
            near, brk = params
            run_name = trades_path.parent.name
            for row in analyze_trades(trades_path, near, brk):
                row['扫参运行名'] = run_name
                all_rows.append(row)

    if not all_rows:
        print('未找到可分析的 trades.csv')
        return 1

    out_df = pd.DataFrame(all_rows)

    # 同一 (near, break, 持仓类型) 若仅对应一次回测，去掉辅助列；若多次（如 v1/v2 同参）保留扫参运行名
    dup_keys = out_df.groupby(['OSC_NEAR_REF_THRESHOLD', 'OSC_BREAK_REF_THRESHOLD', '持仓类型']).size()
    has_dup = (dup_keys > 1).any()
    sort_cols = ['OSC_NEAR_REF_THRESHOLD', 'OSC_BREAK_REF_THRESHOLD', '持仓类型']
    if has_dup:
        sort_cols = ['扫参运行名'] + sort_cols
    out_df = out_df.sort_values(sort_cols, kind='stable').reset_index(drop=True)

    out_path = Path(args.output) if args.output else default_out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    write_df = out_df.copy()
    write_df['平均盈亏比例'] = write_df['平均盈亏比例'].map(format_ratio)
    if not has_dup:
        write_df = write_df[OUT_FIELDS]
    else:
        write_df = write_df[['扫参运行名'] + OUT_FIELDS]
    write_df.to_csv(out_path, index=False, encoding='utf-8-sig', float_format='%.6f')

    print(f'分析 run 数: {len(seen_runs)} | 跳过: {skipped}')
    print(f'汇总行数: {len(write_df)}')
    print(f'输出: {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
