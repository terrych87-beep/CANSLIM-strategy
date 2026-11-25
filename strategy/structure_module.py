"""日线中枢/笔/range 结构计算模块。

输入参数：
  - 日线 DataFrame 或 symbol、StructCfg、as_of（运行点时间）
输出参数：
  - structure bundle dict（center、range_high/low、strokes、extend_cnt 等）
  - 可视化/日志用 format_structure_* 文本
用途：
  - 9:00 盘前结构快照（截止前一交易日收盘）；特征表预计算同源

使用说明：
  - build_daily_bundle_from_daily_df() 为特征表/回测核心 API
  - StructureModule.build_daily_bundle() 为策略运行时封装
  - build_daily_bundle_for_viz() 供 plot_structure 全历史可视化
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from engine.data_portal import DataPortal


logger = logging.getLogger(__name__)


@dataclass
class StructureSnapshot:
    center: Optional[dict]
    range_high: Optional[float]
    range_low: Optional[float]
    range_pct: Optional[float]
    range_state: bool
    extend_cnt: int
    last_close: Optional[float]
    live_index_price: Optional[float] = None


# StructCfg 说明
# 输入参数：STRUCT_ATR_N/K、STRUCT_WINDOW、STRUCT_CENTER_BREAK 等字段
# 输出参数：StructCfg 实例
# 用途：中枢/笔/range 算法参数（与特征表 bundle 绑定）
@dataclass
class StructCfg:
    STRUCT_ATR_N: int = 14
    STRUCT_ATR_K: float = 1.2
    STRUCT_MIN_CONFIRM_GAP: int = 1
    STRUCT_MIN_STROKE_LEN: int = 2
    STRUCT_FLOAT_EPS: float = 1e-8
    STRUCT_CENTER_BREAK: float = 0.02
    STRUCT_WINDOW: int = 320


_STRUCT_STATE_CACHE: dict[str, dict] = {}
_STRUCT_STATE_META: dict[str, dict] = {}


def _fmt_struct_dt(x) -> str:
    if x is None:
        return 'None'
    try:
        return pd.Timestamp(x).strftime('%Y-%m-%d')
    except Exception:
        return str(x)


def _f2(v) -> str:
    if v is None:
        return 'None'
    try:
        return f'{float(v):.2f}'
    except Exception:
        return str(v)


def _struct_add_atr(df: pd.DataFrame, n: int) -> pd.DataFrame:
    d = df.copy()
    high = d['high'].astype(float)
    low = d['low'].astype(float)
    close = d['close'].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    d['atr'] = tr.rolling(n, min_periods=n).mean()
    return d


def _struct_make_stroke(idx, highs, lows, a, b, confirmed: bool, end_price_override=None, dir_override=None, min_stroke_len: int | None = None):
    si = int(a['i'])
    ei = int(b['i'])
    if ei <= si:
        return None
    bar_gap = int(ei - si)
    min_len = int(min_stroke_len if min_stroke_len is not None else StructCfg().STRUCT_MIN_STROKE_LEN)
    if bar_gap < min_len:
        return None
    start_dt = idx[si]
    end_dt = idx[ei]
    start_p = float(a['price'])
    end_p = float(end_price_override) if end_price_override is not None else float(b['price'])
    ddir = dir_override or ('up' if end_p >= start_p else 'down')
    seg_high = float(max(highs[si:ei + 1]))
    seg_low = float(min(lows[si:ei + 1]))
    rel_high_i = max(range(si, ei + 1), key=lambda j: float(highs[j]))
    rel_low_i = min(range(si, ei + 1), key=lambda j: float(lows[j]))
    return {'dir': ddir, 'start_i': si, 'end_i': ei, 'start_dt': start_dt, 'end_dt': end_dt, 'start_p': start_p, 'end_p': end_p, 'high': seg_high, 'low': seg_low, 'high_dt': idx[rel_high_i], 'low_dt': idx[rel_low_i], 'pivot_confirm_dt': b.get('dt'), 'length': bar_gap, 'confirmed': bool(confirmed)}


def _struct_merge_same_direction_strokes(strokes: List[dict]) -> List[dict]:
    if not strokes:
        return []
    out = []
    for s in strokes:
        if not out:
            out.append(dict(s))
            continue
        last = out[-1]
        if last.get('dir') == s.get('dir'):
            new_s = dict(last)
            new_s['end_i'] = s.get('end_i', new_s.get('end_i'))
            new_s['end_dt'] = s['end_dt']
            new_s['end_p'] = s['end_p']
            new_s['length'] = int(new_s['end_i'] - new_s['start_i']) if new_s.get('start_i') is not None and new_s.get('end_i') is not None else int(new_s.get('length', 0) or 0) + int(s.get('length', 0) or 0)
            new_s['confirmed'] = bool(last.get('confirmed', True)) and bool(s.get('confirmed', True))
            if float(s['high']) >= float(new_s['high']):
                new_s['high'] = float(s['high'])
                new_s['high_dt'] = s.get('high_dt')
            if float(s['low']) <= float(new_s['low']):
                new_s['low'] = float(s['low'])
                new_s['low_dt'] = s.get('low_dt')
            out[-1] = new_s
        else:
            out.append(dict(s))
    return out


def _struct_build_atr_zigzag_strokes(df: pd.DataFrame, cfg: StructCfg):
    work = df.dropna(subset=['atr']).copy()
    if len(work) < 30:
        return [], [], None
    idx = list(work.index)
    highs = list(work['high'].astype(float).values)
    lows = list(work['low'].astype(float).values)
    closes = list(work['close'].astype(float).values)
    atrs = list(work['atr'].values)
    pivots = []

    def add_pivot(p):
        if not pivots:
            pivots.append(p)
            return
        last = pivots[-1]
        if p['i'] == last['i']:
            return
        if p['type'] == last['type']:
            if p['type'] == 'top' and float(p['price']) > float(last['price']):
                pivots[-1] = p
            elif p['type'] == 'bottom' and float(p['price']) < float(last['price']):
                pivots[-1] = p
            return
        pivots.append(p)

    trend = None
    last_confirm_i = 0
    cand_high_i = cand_high_p = None
    cand_low_i = cand_low_p = None
    for i in range(1, len(work)):
        h = float(highs[i])
        l = float(lows[i])
        atr = float(atrs[i]) if atrs[i] == atrs[i] else None
        if atr is None or atr <= 0:
            continue
        threshold = float(cfg.STRUCT_ATR_K) * atr
        if trend is None:
            if cand_high_p is None or h > cand_high_p:
                cand_high_p, cand_high_i = h, i
            if cand_low_p is None or l < cand_low_p:
                cand_low_p, cand_low_i = l, i
            if cand_low_p is not None and (h - cand_low_p) >= threshold and (i - last_confirm_i) >= cfg.STRUCT_MIN_CONFIRM_GAP:
                add_pivot({'i': cand_low_i, 'dt': idx[cand_low_i], 'price': cand_low_p, 'type': 'bottom'})
                trend = 'up'
                last_confirm_i = i
                cand_low_i = cand_low_p = None
                cand_high_i, cand_high_p = i, h
                continue
            if cand_high_p is not None and (cand_high_p - l) >= threshold and (i - last_confirm_i) >= cfg.STRUCT_MIN_CONFIRM_GAP:
                add_pivot({'i': cand_high_i, 'dt': idx[cand_high_i], 'price': cand_high_p, 'type': 'top'})
                trend = 'down'
                last_confirm_i = i
                cand_high_i = cand_high_p = None
                cand_low_i, cand_low_p = i, l
                continue
            continue
        if trend == 'up':
            if cand_high_p is None:
                cand_high_p, cand_high_i = h, i
            elif (cand_high_p - l) < threshold and h > cand_high_p:
                cand_high_p, cand_high_i = h, i
            if cand_high_p is not None and (cand_high_p - l) >= threshold and (i - last_confirm_i) >= cfg.STRUCT_MIN_CONFIRM_GAP:
                add_pivot({'i': cand_high_i, 'dt': idx[cand_high_i], 'price': cand_high_p, 'type': 'top'})
                trend = 'down'
                last_confirm_i = i
                cand_high_i = cand_high_p = None
                cand_low_i, cand_low_p = i, l
                continue
        else:
            if cand_low_p is None:
                cand_low_p, cand_low_i = l, i
            elif (h - cand_low_p) < threshold and l < cand_low_p:
                cand_low_p, cand_low_i = l, i
            if cand_low_p is not None and (h - cand_low_p) >= threshold and (i - last_confirm_i) >= cfg.STRUCT_MIN_CONFIRM_GAP:
                add_pivot({'i': cand_low_i, 'dt': idx[cand_low_i], 'price': cand_low_p, 'type': 'bottom'})
                trend = 'up'
                last_confirm_i = i
                cand_low_i = cand_low_p = None
                cand_high_i, cand_high_p = i, h
                continue
    raw_strokes = []
    for a, b in zip(pivots[:-1], pivots[1:]):
        if a['type'] == b['type']:
            continue
        s = _struct_make_stroke(idx, highs, lows, a, b, confirmed=True, min_stroke_len=cfg.STRUCT_MIN_STROKE_LEN)
        if s is not None:
            raw_strokes.append(s)
    strokes = _struct_merge_same_direction_strokes(raw_strokes)
    pending_stroke = None
    if pivots:
        last_p = pivots[-1]
        end_i = len(work) - 1
        start_i = last_p['i']
        if end_i > start_i:
            last_close = float(closes[-1])
            pending_dir = 'up' if last_close >= float(last_p['price']) else 'down'
            pending_stroke = _struct_make_stroke(idx=idx, highs=highs, lows=lows, a=last_p, b={'i': end_i, 'dt': idx[-1], 'price': last_close, 'type': 'top'}, confirmed=False, end_price_override=last_close, dir_override=pending_dir, min_stroke_len=cfg.STRUCT_MIN_STROKE_LEN)
    current_strokes = list(strokes)
    if pending_stroke is not None and pending_stroke.get('length', 0) >= cfg.STRUCT_MIN_STROKE_LEN:
        current_strokes.append(pending_stroke)
    return pivots, strokes, pending_stroke, _struct_merge_same_direction_strokes(current_strokes)


def _struct_make_center_from_three(strokes3):
    dirs = [s['dir'] for s in strokes3]
    if dirs not in (['up', 'down', 'up'], ['down', 'up', 'down']):
        return None
    zg = min(s['high'] for s in strokes3)
    zd = max(s['low'] for s in strokes3)
    if not (float(zd) < float(zg)):
        return None
    return {'ZG': float(zg), 'ZD': float(zd), 'start_dt': strokes3[0]['start_dt'], 'end_dt': strokes3[-1]['end_dt'], 'strokes_used': list(strokes3), 'extend_cnt': 0}


def _struct_init_structure_state() -> dict:
    return {'center': None, 'range_high': None, 'range_low': None, 'range_high_dt': None, 'range_low_dt': None, 'extend_cnt': 0, 'last_processed_stroke_dt': None}


def _struct_normalize_structure_state(st: dict | None) -> dict:
    base = _struct_init_structure_state()
    if isinstance(st, dict):
        for k in base.keys():
            if k in st:
                base[k] = st.get(k)
    return base


def _struct_init_range_from_center(center, cfg: StructCfg):
    rh = float(center['ZG'])
    rl = float(center['ZD'])
    rh_dt = None
    rl_dt = None
    eps = cfg.STRUCT_FLOAT_EPS
    strokes_used = center.get('strokes_used') or []
    for s in strokes_used:
        if abs(float(s['high']) - float(rh)) < eps:
            rh_dt = s.get('high_dt') or s.get('end_dt')
            break
    for s in strokes_used:
        if abs(float(s['low']) - float(rl)) < eps:
            rl_dt = s.get('low_dt') or s.get('end_dt')
            break
    return float(rh), float(rl), rh_dt, rl_dt


def _struct_recent_three_center(strokes):
    if not strokes or len(strokes) < 3:
        return None
    tri = list(strokes[-3:])
    dirs = [s['dir'] for s in tri]
    if dirs not in (['up', 'down', 'up'], ['down', 'up', 'down']):
        return None
    zg = min(float(s['high']) for s in tri)
    zd = max(float(s['low']) for s in tri)
    if not (zd < zg):
        return None
    return {'ZG': float(zg), 'ZD': float(zd)}


def _struct_normalize_strokes_used(strokes_used: list[dict]) -> list[dict]:
    if not strokes_used:
        return []
    out = []
    for s in strokes_used:
        if not out:
            out.append(dict(s))
            continue
        last = out[-1]
        same_group = last.get('start_dt') == s.get('start_dt') and last.get('dir') == s.get('dir')
        if same_group:
            try:
                le = pd.to_datetime(last.get('end_dt'))
                se = pd.to_datetime(s.get('end_dt'))
                if se >= le:
                    out[-1] = dict(s)
            except Exception:
                out[-1] = dict(s)
        else:
            out.append(dict(s))
    return out


def _struct_price_breaks_range(price, rh, rl, cfg: StructCfg):
    if rh is None or rl is None or price is None:
        return False
    p = float(price)
    return p >= float(rh) * (1 + cfg.STRUCT_CENTER_BREAK) or p <= float(rl) * (1 - cfg.STRUCT_CENTER_BREAK)


def _struct_build_active_center_ref(current_strokes):
    if not current_strokes or len(current_strokes) < 3:
        return None
    active = None
    seg = []
    for s in current_strokes:
        seg.append(s)
        if active is None:
            if len(seg) >= 3:
                cand = _struct_make_center_from_three(seg[-3:])
                if cand is not None:
                    active = cand
            continue
        new_zg = min(float(active['ZG']), float(s['high']))
        new_zd = max(float(active['ZD']), float(s['low']))
        if new_zd < new_zg:
            active['ZG'] = float(new_zg)
            active['ZD'] = float(new_zd)
            active['end_dt'] = s['end_dt']
            active['strokes_used'].append(s)
            active['extend_cnt'] = int(active.get('extend_cnt', 0) or 0) + 1
            continue
        active = None
        seg = seg[-3:]
        if len(seg) >= 3:
            cand = _struct_make_center_from_three(seg[-3:])
            if cand is not None:
                active = cand
    return active


def _struct_build_range_from_center_ref(center, cfg: StructCfg):
    if center is None:
        return None, None, None, None, 0
    strokes_used = list(center.get('strokes_used') or [])
    if len(strokes_used) < 3:
        return None, None, None, None, 0
    base = _struct_make_center_from_three(strokes_used[:3])
    if base is None:
        return None, None, None, None, 0
    rh = float(base['ZG'])
    rl = float(base['ZD'])
    extend_cnt = 0
    for k in range(3, len(strokes_used)):
        tri = strokes_used[k - 2:k + 1]
        rc = _struct_make_center_from_three(tri)
        if rc is None:
            continue
        rh = max(float(rh), float(rc['ZG']))
        rl = min(float(rl), float(rc['ZD']))
        extend_cnt += 1
    rh_dt = rl_dt = None
    eps = cfg.STRUCT_FLOAT_EPS
    for s in strokes_used:
        if abs(float(s['high']) - float(rh)) < eps:
            rh_dt = s.get('high_dt') or s.get('end_dt')
            break
    for s in strokes_used:
        if abs(float(s['low']) - float(rl)) < eps:
            rl_dt = s.get('low_dt') or s.get('end_dt')
            break
    return float(rh), float(rl), rh_dt, rl_dt, int(extend_cnt)


def _compute_range_pct_and_state(px, rh, rl):
    if rh is None or rl is None or px is None:
        return None, False
    px = float(px)
    rh = float(rh)
    rl = float(rl)
    range_pct = (rh - rl) / rl if rl > 0 else None
    in_range = (px > rl) and (px < rh)
    return range_pct, in_range


def _resolve_local_daily_path(symbol: str) -> Path:
    base = Path(__file__).resolve().parents[1] / 'data'
    candidates = [
        base / f'{symbol}_日线.csv',
        base / f'kline_{symbol}_1d_merged.csv',
        base / f'{symbol}日线.csv',
        base / f'{symbol}_日线加入实时数据.csv',
        base / f'{symbol}日线加入实时数据.csv'.replace('000001', '上证指数'),
        base / '上证指数日线加入实时数据.csv',
    ]
    for p in candidates:
        if p.exists():
            return p
    raise RuntimeError(f'未找到本地日线数据，已尝试: {candidates}')


def _load_local_daily_df(symbol: str) -> pd.DataFrame:
    path = _resolve_local_daily_path(symbol)
    df = pd.read_csv(path, encoding='utf-8-sig')
    rename = {
        '交易时间': 'datetime',
        '日期': 'datetime',
        '时间': 'datetime',
        '开盘价': 'open',
        '最高价': 'high',
        '最低价': 'low',
        '收盘价': 'close',
    }
    df = df.rename(columns=rename)
    need_cols = ['datetime', 'open', 'high', 'low', 'close']
    missing = [c for c in need_cols if c not in df.columns]
    if missing:
        raise RuntimeError(f'日线字段缺失: {missing}')
    df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce')
    df = df.dropna(subset=['datetime', 'close']).sort_values('datetime')
    return df


# build_daily_bundle_from_daily_df 方法说明
# 输入参数：df（日线 OHLC）、cfg、as_of（按 as_of 前一交易日止切片）
# 输出参数：dict bundle（center、range、strokes、pending_stroke、current_strokes 等）
# 用途：结构快照核心算法，特征表与回测 9:00 共用
def build_daily_bundle_from_daily_df(
    df: pd.DataFrame,
    cfg: StructCfg,
    as_of: datetime | pd.Timestamp | None = None,
    *,
    update_cache: bool = True,
    cache_key: str = 'default',
) -> Dict[str, Any]:
    """基于已加载的日线 DataFrame 构建结构快照（与 build_daily_bundle_from_local 同源算法）。"""
    work = df.copy()
    if 'datetime' not in work.columns:
        raise RuntimeError('日线 DataFrame 缺少 datetime 列')
    work['datetime'] = pd.to_datetime(work['datetime'], errors='coerce')
    work = work.dropna(subset=['datetime', 'close']).sort_values('datetime')
    base_ts = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now()
    req_end_date = base_ts.date() - timedelta(days=1)
    df2 = work[work['datetime'].dt.date <= req_end_date].copy()
    if len(df2) < 100:
        df2 = work.copy()
    if len(df2) < 100:
        raise RuntimeError('日线数据不足，无法构建结构')
    df2 = df2.set_index('datetime')
    need = cfg.STRUCT_WINDOW + cfg.STRUCT_ATR_N + 10
    dfw = df2.tail(need).copy() if len(df2) > need else df2.copy()
    dfw = _struct_add_atr(dfw, cfg.STRUCT_ATR_N)
    pivots, strokes, pending, current_strokes = _struct_build_atr_zigzag_strokes(dfw, cfg)
    last_close = float(dfw['close'].iloc[-1])
    center = _struct_build_active_center_ref(current_strokes)
    rh = rl = rh_dt = rl_dt = None
    extend_cnt = 0
    if center is not None:
        rh, rl, rh_dt, rl_dt, extend_cnt = _struct_build_range_from_center_ref(center, cfg)
        if _struct_price_breaks_range(last_close, rh, rl, cfg):
            center = None
            rh = rl = rh_dt = rl_dt = None
            extend_cnt = 0
    last_processed_stroke_dt = current_strokes[-1].get('end_dt') if current_strokes else None
    if update_cache:
        current_last_trade_day = pd.Timestamp(dfw.index[-1]).date()
        structure_state = _struct_normalize_structure_state(_STRUCT_STATE_CACHE.get(cache_key))
        structure_state['center'] = center
        structure_state['range_high'] = rh
        structure_state['range_low'] = rl
        structure_state['range_high_dt'] = rh_dt
        structure_state['range_low_dt'] = rl_dt
        structure_state['extend_cnt'] = int(extend_cnt)
        structure_state['last_processed_stroke_dt'] = last_processed_stroke_dt
        _STRUCT_STATE_CACHE[cache_key] = dict(structure_state)
        _STRUCT_STATE_META[cache_key] = {'last_trade_day': current_last_trade_day}
    range_pct, in_range = _compute_range_pct_and_state(last_close, rh, rl)
    return {
        'structure_mode': 'full_rebuild_base',
        'last_processed_stroke_dt': last_processed_stroke_dt,
        'req_end_date': req_end_date,
        'last_trade_date': dfw.index[-1],
        'last_close': last_close,
        'center': center,
        'range_high': rh,
        'range_low': rl,
        'range_high_dt': rh_dt,
        'range_low_dt': rl_dt,
        'range_pct': range_pct,
        'extend_cnt': int(extend_cnt),
        'range_state': bool(in_range),
        'pivots': pivots,
        'strokes': strokes,
        'pending_stroke': pending,
        'current_strokes': current_strokes,
    }


# build_daily_bundle_for_viz 方法说明
# 输入参数：daily_df、cfg、hover_date（悬停日）
# 输出参数：dict bundle（全历史笔，含 pending_stroke 虚线口径）
# 用途：scripts/plot_structure.py 交互可视化
def build_daily_bundle_for_viz(
    df: pd.DataFrame,
    cfg: StructCfg,
    trade_date: datetime | pd.Timestamp | date,
) -> Dict[str, Any]:
    """可视化口径：截止 trade_date 当日收盘（含当日 K 线），自数据首日至当日的全量日线重算笔（不截断 STRUCT_WINDOW）。"""
    work = df.copy()
    if 'datetime' not in work.columns:
        raise RuntimeError('日线 DataFrame 缺少 datetime 列')
    work['datetime'] = pd.to_datetime(work['datetime'], errors='coerce')
    work = work.dropna(subset=['datetime', 'close']).sort_values('datetime')
    end_d = pd.Timestamp(trade_date).date()
    df2 = work[work['datetime'].dt.date <= end_d].copy()
    if len(df2) < max(cfg.STRUCT_ATR_N + 30, 80):
        raise RuntimeError('日线数据不足，无法构建结构')
    dfw = df2.set_index('datetime')
    dfw = _struct_add_atr(dfw, cfg.STRUCT_ATR_N)
    pivots, strokes, pending, current_strokes = _struct_build_atr_zigzag_strokes(dfw, cfg)
    last_close = float(dfw['close'].iloc[-1])
    center = _struct_build_active_center_ref(current_strokes)
    rh = rl = rh_dt = rl_dt = None
    extend_cnt = 0
    if center is not None:
        rh, rl, rh_dt, rl_dt, extend_cnt = _struct_build_range_from_center_ref(center, cfg)
        if _struct_price_breaks_range(last_close, rh, rl, cfg):
            center = None
            rh = rl = rh_dt = rl_dt = None
            extend_cnt = 0
    last_processed_stroke_dt = current_strokes[-1].get('end_dt') if current_strokes else None
    range_pct, in_range = _compute_range_pct_and_state(last_close, rh, rl)
    return {
        'structure_mode': 'viz_full_history',
        'last_processed_stroke_dt': last_processed_stroke_dt,
        'req_end_date': end_d,
        'last_trade_date': dfw.index[-1],
        'last_close': last_close,
        'center': center,
        'range_high': rh,
        'range_low': rl,
        'range_high_dt': rh_dt,
        'range_low_dt': rl_dt,
        'range_pct': range_pct,
        'extend_cnt': int(extend_cnt),
        'range_state': bool(in_range),
        'pivots': pivots,
        'strokes': strokes,
        'pending_stroke': pending,
        'current_strokes': current_strokes,
    }


def format_structure_tooltip(bundle: Dict[str, Any]) -> str:
    """悬停日结构文案（与 9:00 日志字段一致，截止日为 last_trade_date 收盘）。"""
    c = bundle.get('center')
    if c is None:
        return (
            '【中枢】无（未形成有效结构或已失效）\n'
            '【区间】无'
        )
    rh = bundle.get('range_high')
    rl = bundle.get('range_low')
    rp = bundle.get('range_pct')
    rp_s = f'{float(rp) * 100:.2f}%' if rp is not None else '—'
    strokes_used = list(c.get('strokes_used') or [])
    r0 = _fmt_struct_dt(strokes_used[0].get('start_dt')) if strokes_used else '—'
    r1 = _fmt_struct_dt(strokes_used[-1].get('end_dt')) if strokes_used else '—'
    return (
        f"【中枢】ZG={_f2(c['ZG'])} ZD={_f2(c['ZD'])} "
        f"扩展={int(bundle.get('extend_cnt', 0) or 0)} "
        f"起止={_fmt_struct_dt(c.get('start_dt'))}→{_fmt_struct_dt(c.get('end_dt'))}\n"
        f"【区间】上沿={_f2(rh)} 下沿={_f2(rl)} "
        f"起止={r0}→{r1} 幅度={rp_s}"
    )


def build_daily_bundle_from_local(symbol: str, cfg: StructCfg, as_of: datetime | pd.Timestamp | None = None) -> Dict[str, Any]:
    df = _load_local_daily_df(symbol)
    return build_daily_bundle_from_daily_df(df, cfg, as_of, update_cache=True, cache_key=str(symbol))


class StructureModule:
    # 模块说明
    # 输入参数：cfg、data_portal（可选）；可选 impl 注入
    # 输出参数：StructureModule 实例
    # 用途：策略层结构构建封装，委托 build_daily_bundle_from_local
    def __init__(self, cfg: Optional[Any] = None, data_portal: Optional[DataPortal] = None, impl: Optional[Any] = None):
        self.cfg = cfg or StructCfg()
        self.data_portal = data_portal
        self.impl = impl

    # build_daily_bundle 方法说明
    # 输入参数：as_of、symbol（默认 cfg.STRUCT_SYMBOL）
    # 输出参数：dict bundle
    # 用途：策略 9:00 构建结构快照（无特征表 fallback）
    def build_daily_bundle(self, as_of: datetime | pd.Timestamp | None = None, symbol: Optional[str] = None) -> Dict[str, Any]:
        if self.impl is not None:
            return self.impl.build_daily_bundle(as_of)
        symbol = symbol or getattr(self.cfg, 'STRUCT_SYMBOL', '000001')
        return build_daily_bundle_from_local(symbol, self.cfg, as_of)


def format_structure_9am_lines(now: datetime, bundle: Dict[str, Any], trade_contract: str) -> List[str]:
    t = now.strftime('%Y-%m-%d %H:%M:%S')
    c = bundle.get('center')
    idx_px = bundle.get('last_close')
    lines: List[str] = []
    lines.append(f"{t} - INFO  - 【结构】日线截止(请求)={bundle.get('req_end_date')} 最后交易日={_fmt_struct_dt(bundle.get('last_trade_date'))} 参考价={_f2(idx_px)}")
    lines.append('')
    if c is None:
        lines.append(f"{t} - INFO  - 【中枢】当前无中枢（笔序列未形成有效结构或已失效）")
        lines.append('')
        lines.append(f"{t} - INFO  - 【range】range_high=None range_low=None")
        lines.append('')
        lines.append(f"{t} - INFO  - 【位置】无中枢/无 range，无法判定")
        lines.append('')
        lines.append(f"{t} - INFO  - ============================================================")
        lines.append('')
        lines.append(f"{t} - INFO  - 开盘前完成：trade_contract={trade_contract}")
        return lines
    lines.append(f"{t} - INFO  - 【中枢】ZG={_f2(c['ZG'])} ZD={_f2(c['ZD'])} 扩展次数={int(bundle.get('extend_cnt', 0) or 0)} 起止={_fmt_struct_dt(c.get('start_dt'))}→{_fmt_struct_dt(c.get('end_dt'))}")
    lines.append('')
    for j, s in enumerate(c.get('strokes_used') or [], 1):
        lines.append(f"{t} - INFO  -   构成笔{j} {'上' if s.get('dir')=='up' else '下'} {_fmt_struct_dt(s.get('start_dt'))}→{_fmt_struct_dt(s.get('end_dt'))} high={_f2(s.get('high'))} low={_f2(s.get('low'))}")
        lines.append('')
    rh, rl = bundle.get('range_high'), bundle.get('range_low')
    lines.append(f"{t} - INFO  - 【range】range_high={_f2(rh)} range_low={_f2(rl)} range_high_dt={_fmt_struct_dt(bundle.get('range_high_dt'))} range_low_dt={_fmt_struct_dt(bundle.get('range_low_dt'))} range_pct={_f2(bundle.get('range_pct'))}")
    lines.append('')
    zg, zd = float(c['ZG']), float(c['ZD'])
    pos_c = '中枢内' if (idx_px is not None and zd <= float(idx_px) <= zg) else '中枢外'
    px = float(idx_px)
    break_up = rh is not None and px >= float(rh) * 1.01
    break_down = rl is not None and px <= float(rl) * 0.99
    in_range = rh is not None and rl is not None and (px > float(rl)) and (px < float(rh))
    if in_range:
        pos_r = 'range_inside'
    elif break_up:
        pos_r = 'breakout_up'
    elif break_down:
        pos_r = 'breakout_down'
    else:
        pos_r = 'unknown'
    lines.append(f"{t} - INFO  - 【位置】相对中枢={pos_c} 相对range(inside/breakout)={pos_r} range_state={bundle.get('range_state')}")
    lines.append('')
    lines.append(f"{t} - INFO  - ============================================================")
    lines.append(f"{t} - INFO  - 开盘前完成：trade_contract={trade_contract}")
    return lines

# 输入输出约定
# 1) build_daily_bundle_from_local(symbol, cfg, as_of)
#    输入:
#      - symbol: 标的代码字符串，例如 '000001'
#      - cfg: StructCfg，控制 ATR、ZigZag、成枢与失效阈值
#      - as_of: 运行点时间，按 as_of 前一交易日及以前的数据构建结构
#    输出:
#      - dict bundle，包含 center / range / strokes / pending_stroke / current_strokes / last_close / last_trade_date 等字段
#    语义:
#      - strokes = 已确认笔（主干）
#      - pending_stroke = 临时尾笔，仅做候选观察
#      - current_strokes = 用于展示和辅助判断的当前笔序列
# 2) format_structure_9am_lines(now, bundle, trade_contract)
#    输入:
#      - now: 当前运行点时间
#      - bundle: 上述结构 bundle
#      - trade_contract: 开盘前拟交易合约
#    输出:
#      - List[str] 逐行日志文本，供 09:00 输出或报表落盘
