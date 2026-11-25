# 模块说明
# 输入参数：StructCfg、日线 DataFrame、视图日期区间
# 输出参数：ECharts HTML；缓存至 outputs/structure/cache/{viz_bundle_id}/
# 中文映射：
#   - viz_bundle_id -> sha256(struct_cfg + daily_file)[:16]
#   - snapshots/YYYY-MM-DD.pkl -> 按日结构快照
# 用途：structure_viz_core 供 plot_structure.py 与 网页结构可视化.py 共用
# 使用说明：一般不直接运行；import render_structure_html 等

from __future__ import annotations

import hashlib
import json
import pickle
import tempfile
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from engine.data_portal import DataPortal
from strategy.feature_store import _file_snapshot
from strategy.structure_module import StructCfg, format_structure_tooltip

# 绘图常量与工具（原 plot_structure.py）
ECHARTS = 'https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js'
COLOR_UP = '#ef5350'
COLOR_DOWN = '#26a69a'
COLOR_STROKE = '#0d47a1'
COLOR_STROKE_PENDING = '#1565c0'
CENTER_FILL = 'rgba(13, 71, 161, 0.38)'
CENTER_BORDER = 'rgba(13, 71, 161, 0.95)'
RANGE_PALETTE = [
    ('rgba(230, 81, 0, 0.42)', 'rgba(191, 54, 12, 0.95)'),
    ('rgba(255, 143, 0, 0.32)', 'rgba(230, 81, 0, 0.88)'),
    ('rgba(255, 183, 77, 0.26)', 'rgba(255, 143, 0, 0.82)'),
    ('rgba(255, 204, 128, 0.22)', 'rgba(255, 167, 38, 0.78)'),
    ('rgba(255, 224, 178, 0.20)', 'rgba(255, 183, 77, 0.72)'),
]

DEFAULT_CACHE_ROOT = Path('outputs') / 'structure' / 'cache'
DEFAULT_HTML_ROOT = Path('outputs') / 'structure'
SNAPSHOTS_SUBDIR = 'snapshots'
LEGACY_SNAPSHOTS_NAME = 'snapshots.pkl'
MANIFEST_NAME = 'manifest.json'

ProgressCb = Callable[[str], None] | None


def viz_bundle_id(struct_cfg: StructCfg, daily_file: str) -> str:
    payload = json.dumps(
        {'struct_cfg': asdict(struct_cfg), 'daily_file': daily_file},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _ts_ms(x: Any) -> int | None:
    if x is None:
        return None
    return int(pd.Timestamp(x).timestamp() * 1000)


def load_daily_ohlc(portal: DataPortal, daily_file: str) -> pd.DataFrame:
    df = portal.load_frame(daily_file).copy()
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
    df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce')
    return df.dropna(subset=['datetime', 'open', 'high', 'low', 'close']).sort_values('datetime')


def _center_key(center: dict | None) -> tuple | None:
    if not center:
        return None
    return (
        round(float(center['ZG']), 4),
        round(float(center['ZD']), 4),
        str(pd.Timestamp(center.get('start_dt')).date()) if center.get('start_dt') else None,
    )


def _range_key(bundle: dict[str, Any]) -> tuple | None:
    c = bundle.get('center')
    rh, rl = bundle.get('range_high'), bundle.get('range_low')
    if c is None or rh is None or rl is None:
        return None
    return (
        round(float(rh), 4),
        round(float(rl), 4),
        int(bundle.get('extend_cnt', 0) or 0),
    )


def _range_time_span(bundle: dict[str, Any]) -> tuple[Any, Any] | None:
    c = bundle.get('center') or {}
    strokes_used = list(c.get('strokes_used') or [])
    if not strokes_used:
        return None
    return strokes_used[0].get('start_dt'), strokes_used[-1].get('end_dt')


def _box_mark_area(x0: Any, x1: Any, y_low: float, y_high: float, fill: str, border: str) -> list[dict]:
    t0, t1 = _ts_ms(x0), _ts_ms(x1)
    if t0 is None or t1 is None:
        return []
    return [
        {
            'xAxis': t0,
            'yAxis': float(y_low),
            'itemStyle': {'color': fill, 'borderColor': border, 'borderWidth': 1.5},
            'label': {'show': False},
        },
        {'xAxis': t1, 'yAxis': float(y_high)},
    ]


def _stroke_segments(
    strokes: list[dict],
    *,
    view_start: pd.Timestamp,
    view_end: pd.Timestamp,
) -> list[list]:
    out: list[list] = []
    v0 = int(view_start.timestamp() * 1000)
    v1 = int(view_end.timestamp() * 1000)
    for s in strokes:
        sd, ed = s.get('start_dt'), s.get('end_dt')
        if sd is None or ed is None:
            continue
        t0, t1 = _ts_ms(sd), _ts_ms(ed)
        if t0 is None or t1 is None:
            continue
        if t1 < v0 or t0 > v1:
            continue
        sp, ep = float(s.get('start_p', 0)), float(s.get('end_p', 0))
        if out:
            out.append([None, None])
        out.append([t0, sp])
        out.append([t1, ep])
    return out


def precompute_daily_snapshots(
    daily_df: pd.DataFrame,
    struct_cfg: StructCfg,
    trade_dates: list,
    *,
    progress_cb: ProgressCb = None,
) -> dict[str, dict[str, Any]]:
    from strategy.structure_module import build_daily_bundle_for_viz

    snaps: dict[str, dict[str, Any]] = {}
    n = len(trade_dates)
    for i, td in enumerate(trade_dates, 1):
        key = pd.Timestamp(td).date().isoformat()
        try:
            snaps[key] = build_daily_bundle_for_viz(daily_df, struct_cfg, pd.Timestamp(td).date())
        except Exception:
            snaps[key] = {}
        msg = f'结构快照 {i}/{n} {key}'
        if progress_cb:
            progress_cb(msg)
        elif i == 1 or i == n or i % 200 == 0:
            print(f'  {msg}', flush=True)
    return snaps


def build_center_segments(snaps: dict[str, dict], trade_dates: list) -> list[list]:
    segments: list[list] = []
    active_key = None
    last_bundle: dict | None = None

    def flush():
        nonlocal last_bundle, active_key
        if active_key is None or last_bundle is None:
            return
        c = last_bundle.get('center')
        if not c:
            return
        areas = _box_mark_area(
            c.get('start_dt'), c.get('end_dt'),
            float(c['ZD']), float(c['ZG']),
            CENTER_FILL, CENTER_BORDER,
        )
        if areas:
            segments.append(areas)

    for td in trade_dates:
        key = pd.Timestamp(td).date().isoformat()
        b = snaps.get(key) or {}
        ck = _center_key(b.get('center'))
        if ck != active_key:
            flush()
            active_key = ck
            last_bundle = b if ck else None
        elif ck is not None:
            last_bundle = b

    flush()
    return segments


def build_range_segments(snaps: dict[str, dict], trade_dates: list) -> list[list]:
    segments: list[list] = []
    active_key = None
    last_bundle: dict | None = None

    def flush():
        nonlocal last_bundle, active_key
        if active_key is None or last_bundle is None:
            return
        rh, rl = last_bundle.get('range_high'), last_bundle.get('range_low')
        if rh is None or rl is None:
            return
        span = _range_time_span(last_bundle)
        if not span:
            return
        ext = int(last_bundle.get('extend_cnt', 0) or 0)
        fill, border = RANGE_PALETTE[min(ext, len(RANGE_PALETTE) - 1)]
        areas = _box_mark_area(span[0], span[1], float(rl), float(rh), fill, border)
        if areas:
            segments.append(areas)

    for td in trade_dates:
        key = pd.Timestamp(td).date().isoformat()
        b = snaps.get(key) or {}
        rk = _range_key(b)
        if rk != active_key:
            flush()
            active_key = rk
            last_bundle = b if rk else None
        elif rk is not None:
            last_bundle = b

    flush()
    return segments


def build_stroke_data(
    snaps: dict[str, dict],
    trade_dates: list,
    view_start: pd.Timestamp,
    view_end: pd.Timestamp,
) -> tuple[list, list]:
    end_key = pd.Timestamp(trade_dates[-1]).date().isoformat()
    end_bundle = snaps.get(end_key) or {}
    confirmed = list(end_bundle.get('strokes') or [])
    solid = _stroke_segments(confirmed, view_start=view_start, view_end=view_end)

    dashed_pts: list[list] = []
    for td in trade_dates:
        key = pd.Timestamp(td).date().isoformat()
        b = snaps.get(key) or {}
        pending = b.get('pending_stroke')
        if not pending:
            continue
        segs = _stroke_segments([pending], view_start=view_start, view_end=view_end)
        if not segs:
            continue
        if dashed_pts:
            dashed_pts.append([None, None])
        dashed_pts.extend(segs)

    return solid, dashed_pts


def render_html(
    ohlc_df: pd.DataFrame,
    center_areas: list,
    range_areas: list,
    solid_strokes: list,
    dashed_strokes: list,
    tooltips: dict[str, str],
    *,
    title: str,
    struct_cfg: StructCfg,
) -> str:
    candles = [
        [
            int(pd.Timestamp(r.datetime).timestamp() * 1000),
            float(r.open), float(r.close), float(r.low), float(r.high),
        ]
        for r in ohlc_df.itertuples(index=False)
    ]
    tips_json = json.dumps(tooltips, ensure_ascii=False)

    option = {
        'title': {'text': title, 'left': 'center'},
        'tooltip': {
            'trigger': 'axis',
            'axisPointer': {'type': 'cross'},
            'confine': True,
        },
        'legend': {
            'data': ['日线', '笔(确认)', '笔(临时)', '中枢', '区间'],
            'top': 28,
        },
        'grid': {'left': 60, 'right': 40, 'top': 70, 'bottom': 60},
        'xAxis': {'type': 'time', 'scale': True},
        'yAxis': {'scale': True, 'splitArea': {'show': True}},
        'dataZoom': [
            {'type': 'inside', 'start': 0, 'end': 100},
            {'type': 'slider', 'start': 0, 'end': 100},
        ],
        'series': [
            {
                'name': '日线',
                'type': 'candlestick',
                'data': candles,
                'itemStyle': {
                    'color': COLOR_UP, 'color0': COLOR_DOWN,
                    'borderColor': COLOR_UP, 'borderColor0': COLOR_DOWN,
                },
            },
            {
                'name': '笔(确认)',
                'type': 'line',
                'data': solid_strokes,
                'showSymbol': False,
                'lineStyle': {'color': COLOR_STROKE, 'width': 2},
                'itemStyle': {'color': COLOR_STROKE},
                'connectNulls': False,
                'z': 5,
            },
            {
                'name': '笔(临时)',
                'type': 'line',
                'data': dashed_strokes,
                'showSymbol': False,
                'lineStyle': {'color': COLOR_STROKE_PENDING, 'width': 1.5, 'type': 'dashed'},
                'itemStyle': {'color': COLOR_STROKE_PENDING},
                'connectNulls': False,
                'z': 6,
            },
            {
                'name': '中枢',
                'type': 'line',
                'data': [],
                'markArea': {'silent': True, 'data': center_areas},
                'tooltip': {'show': False},
                'z': 2,
            },
            {
                'name': '区间',
                'type': 'line',
                'data': [],
                'markArea': {'silent': True, 'data': range_areas},
                'tooltip': {'show': False},
                'z': 1,
            },
        ],
    }
    cfg_note = json.dumps({f.name: getattr(struct_cfg, f.name) for f in fields(StructCfg)}, ensure_ascii=False)
    opt_json = json.dumps(option, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <script src="{ECHARTS}"></script>
  <style>
    body {{ margin: 0; font-family: "Microsoft YaHei", sans-serif; background: #fafafa; }}
    #chart {{ width: 100%; height: 92vh; }}
    .meta {{ padding: 8px 16px; font-size: 12px; color: #555; white-space: pre-wrap; }}
  </style>
</head>
<body>
  <div class="meta">可视化口径：截止悬停日收盘 · 全历史笔 · StructCfg: {cfg_note}</div>
  <div id="chart"></div>
  <script>
    const STRUCT_TIPS = {tips_json};
    const chart = echarts.init(document.getElementById('chart'));
    const option = {opt_json};
    option.tooltip.formatter = function (params) {{
      if (!params || !params.length) return '';
      const t = params[0].axisValue;
      const d = echarts.time.format(t, '{{yyyy}}-{{MM}}-{{dd}}', false);
      const body = STRUCT_TIPS[d];
      if (body) {{
        return '<div style="white-space:pre-line;font-size:13px;line-height:1.5">'
          + '<b>' + d + ' 收盘结构</b><br/>' + body.replace(/\\n/g, '<br/>') + '</div>';
      }}
      return '<b>' + d + '</b><br/>（该日无结构快照）';
    }};
    chart.setOption(option);
    window.addEventListener('resize', () => chart.resize());
  </script>
</body>
</html>"""


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=path.suffix + '.tmp')
    try:
        with open(fd, 'wb') as f:
            f.write(data)
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _snap_dir(cache_dir: Path) -> Path:
    return cache_dir / SNAPSHOTS_SUBDIR


def _snap_file(cache_dir: Path, date_key: str) -> Path:
    return _snap_dir(cache_dir) / f'{date_key}.pkl'


def _cached_date_keys(cache_dir: Path) -> set[str]:
    d = _snap_dir(cache_dir)
    if not d.exists():
        return set()
    return {p.stem for p in d.glob('*.pkl')}


def _load_snap(cache_dir: Path, date_key: str) -> dict[str, Any]:
    path = _snap_file(cache_dir, date_key)
    if not path.exists():
        return {}
    with path.open('rb') as f:
        return pickle.load(f)


def _save_snap(cache_dir: Path, date_key: str, bundle: dict[str, Any]) -> None:
    _atomic_write_bytes(
        _snap_file(cache_dir, date_key),
        pickle.dumps(bundle, protocol=pickle.HIGHEST_PROTOCOL),
    )


def _load_snaps_for_dates(cache_dir: Path, date_keys: list[str]) -> dict[str, dict[str, Any]]:
    return {k: _load_snap(cache_dir, k) for k in date_keys}


def _remove_legacy_monolithic_cache(cache_dir: Path) -> None:
    """移除旧版单体 snapshots.pkl（中断后可能极大，导致加载卡死）。"""
    legacy = cache_dir / LEGACY_SNAPSHOTS_NAME
    if legacy.exists():
        legacy.unlink(missing_ok=True)


def _write_manifest(
    cache_dir: Path,
    *,
    viz_id: str,
    struct_cfg: StructCfg,
    daily_file: str,
    data_snapshot: dict[str, Any],
    cached_keys: list[str],
    extended: bool,
) -> None:
    keys = sorted(cached_keys)
    manifest = {
        'viz_bundle_id': viz_id,
        'struct_cfg': asdict(struct_cfg),
        'daily_file': daily_file,
        'data_snapshot': data_snapshot,
        'trade_date_start': keys[0] if keys else None,
        'trade_date_end': keys[-1] if keys else None,
        'trade_days': len(keys),
        'built_at': datetime.now().isoformat(timespec='seconds'),
    }
    if extended:
        manifest['extended_at'] = manifest['built_at']
    _atomic_write_bytes(
        cache_dir / MANIFEST_NAME,
        json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'),
    )


def _dates_to_compute(
    all_trade_dates: list,
    cached_keys: set[str],
    view_start: pd.Timestamp,
    view_end: pd.Timestamp,
) -> list:
    """待算交易日：视图区间内缺失 + 数据末端相对缓存的 auto extend。"""
    need: set[pd.Timestamp] = set()
    for d in all_trade_dates:
        key = pd.Timestamp(d).date().isoformat()
        if key in cached_keys:
            continue
        if view_start <= pd.Timestamp(d) <= view_end:
            need.add(pd.Timestamp(d))

    if cached_keys:
        cached_max = pd.Timestamp(sorted(cached_keys)[-1])
        for d in all_trade_dates:
            td = pd.Timestamp(d)
            key = td.date().isoformat()
            if key in cached_keys:
                continue
            if td > cached_max:
                need.add(td)

    return sorted(need, key=lambda x: x)


def ensure_snapshot_cache(
    portal: DataPortal,
    daily_df: pd.DataFrame,
    struct_cfg: StructCfg,
    daily_file: str,
    *,
    view_start: pd.Timestamp,
    view_end: pd.Timestamp,
    cache_root: Path,
    progress_cb: ProgressCb = None,
) -> tuple[dict[str, dict[str, Any]], Path, int]:
    """加载或 extend 快照：视图区间补缺 + 数据末端 auto extend。返回 (view_snapshots, cache_dir, 新算天数)。"""
    from strategy.structure_module import build_daily_bundle_for_viz

    viz_id = viz_bundle_id(struct_cfg, daily_file)
    cache_dir = Path(cache_root) / viz_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    _remove_legacy_monolithic_cache(cache_dir)

    all_trade_dates = portal.get_trade_dates(daily_file)
    cached_keys = _cached_date_keys(cache_dir)
    missing = _dates_to_compute(all_trade_dates, cached_keys, view_start, view_end)

    if missing:
        had_manifest = (cache_dir / MANIFEST_NAME).exists()
        if progress_cb:
            progress_cb(f'缓存 extend：待计算 {len(missing)} 个交易日（bundle={viz_id}）')
        n = len(missing)
        for i, td in enumerate(missing, 1):
            key = pd.Timestamp(td).date().isoformat()
            try:
                bundle = build_daily_bundle_for_viz(daily_df, struct_cfg, pd.Timestamp(td).date())
            except Exception:
                bundle = {}
            _save_snap(cache_dir, key, bundle)
            msg = f'结构快照 {i}/{n} {key}'
            if progress_cb:
                progress_cb(msg)
            elif i == 1 or i == n or i % 200 == 0:
                print(f'  {msg}', flush=True)
        cached_keys = _cached_date_keys(cache_dir)
        data_snap = _file_snapshot(portal, daily_file)
        _write_manifest(
            cache_dir,
            viz_id=viz_id,
            struct_cfg=struct_cfg,
            daily_file=daily_file,
            data_snapshot=data_snap,
            cached_keys=sorted(cached_keys),
            extended=had_manifest,
        )
    elif not (cache_dir / MANIFEST_NAME).exists() and cached_keys:
        data_snap = _file_snapshot(portal, daily_file)
        _write_manifest(
            cache_dir,
            viz_id=viz_id,
            struct_cfg=struct_cfg,
            daily_file=daily_file,
            data_snapshot=data_snap,
            cached_keys=sorted(cached_keys),
            extended=False,
        )

    view_keys = [
        pd.Timestamp(d).date().isoformat()
        for d in all_trade_dates
        if view_start <= pd.Timestamp(d) <= view_end
    ]
    snaps = _load_snaps_for_dates(cache_dir, view_keys)
    return snaps, cache_dir, len(missing)


def run_structure_plot(
    *,
    start_date: str,
    end_date: str,
    data_dir: str | Path,
    struct_cfg: StructCfg,
    daily_file: str = '上证指数日线加入实时数据.csv',
    cache_root: Path | None = None,
    html_root: Path | None = None,
    output: Path | None = None,
    progress_cb: ProgressCb = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    cache_root = Path(cache_root) if cache_root else data_dir.parent / DEFAULT_CACHE_ROOT
    html_root = Path(html_root) if html_root else data_dir.parent / DEFAULT_HTML_ROOT

    portal = DataPortal(str(data_dir))
    start = pd.to_datetime(start_date).normalize()
    end = pd.to_datetime(end_date).normalize()
    daily_df = load_daily_ohlc(portal, daily_file)

    if progress_cb:
        progress_cb('加载/extend 结构快照缓存...')
    snaps, cache_dir, computed_new = ensure_snapshot_cache(
        portal, daily_df, struct_cfg, daily_file,
        view_start=start, view_end=end,
        cache_root=cache_root, progress_cb=progress_cb,
    )

    mask = (daily_df['datetime'] >= start) & (daily_df['datetime'] <= end)
    view_df = daily_df.loc[mask].reset_index(drop=True)
    if view_df.empty:
        raise ValueError('指定区间无日线数据')

    trade_dates = [d for d in portal.get_trade_dates(daily_file) if start <= d <= end]
    if not trade_dates:
        raise ValueError('指定区间无交易日')

    if progress_cb:
        progress_cb(f'渲染 HTML（视图 {len(trade_dates)} 个交易日）...')

    tooltips = {}
    for td in trade_dates:
        key = pd.Timestamp(td).date().isoformat()
        b = snaps.get(key)
        if b:
            tooltips[key] = format_structure_tooltip(b)

    center_areas = build_center_segments(snaps, trade_dates)
    range_areas = build_range_segments(snaps, trade_dates)
    solid, dashed = build_stroke_data(snaps, trade_dates, start, end)

    title = f'日线结构 {start_date} ~ {end_date}'
    html = render_html(
        view_df, center_areas, range_areas, solid, dashed, tooltips,
        title=title, struct_cfg=struct_cfg,
    )

    viz_id = viz_bundle_id(struct_cfg, daily_file)
    if output:
        out = Path(output)
    else:
        out = html_root / (
            f"structure_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}_{viz_id[:8]}.html"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding='utf-8')

    return {
        'output': str(out.resolve()),
        'chart_filename': out.name,
        'viz_bundle_id': viz_id,
        'cache_dir': str(cache_dir.resolve()),
        'cache_trade_days': len(_cached_date_keys(cache_dir)),
        'computed_new': computed_new,
        'view_trade_days': len(trade_dates),
        'center_segments': len(center_areas),
        'range_segments': len(range_areas),
        'solid_points': len(solid),
        'dashed_points': len(dashed),
    }
