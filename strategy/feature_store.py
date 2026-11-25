"""预计算特征表（structure_daily + signals_runpoint parquet）。

输入参数：
  - DataPortal、StructCfg、MacdDivCfg、trade_dates 列表
  - 可选 features_root、日线/30m/1m 文件名
输出参数：
  - FeatureStore 内存索引实例
  - data/features/{bundle_id}/ 下 manifest.json + parquet 文件
用途：
  - 回测/扫参前预计算日线中枢与运行时点 MACD/1m 信号，运行时 lookup 避免重复计算

使用说明：
  - CLI：python scripts/build_features.py --start-date ... --end-date ...
  - 回测：run_backtest.py 默认 resolve_feature_store() 自动加载或 extend
  - bundle_id 由 struct/macd 参数 + FEATURE_SIGNAL_VERSION 决定；逻辑或数据大改请 --rebuild-features
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from engine.data_portal import DataPortal
from engine.event_engine import RUNPOINTS
from strategy.macd_divergence import (
    DivergenceDetector,
    MacdDivCfg,
    detect_1m_top_divergence,
    macd_from_close,
)
from strategy.structure_module import StructCfg, build_daily_bundle_from_daily_df

DEFAULT_FEATURES_ROOT = 'features'
DEFAULT_DAILY_FILE = '上证指数日线加入实时数据.csv'
DEFAULT_30M_FILE = '上证指数30分钟加入实时数据.csv'
DEFAULT_1M_FILE = '上证指数1分钟加入实时数据.csv'
DEFAULT_LOOKBACK_BARS = 260
# 变更后需 --rebuild-features（或 bundle_id 变化会自动新建目录）
FEATURE_SIGNAL_VERSION = 2  # v2: 14:57 精确 1m bar，否则 30m；break_up 与策略一致

SIGNAL_TIME_STRS = [t for t in RUNPOINTS if t not in ('09:00', '14:40', '15:30')]

STRUCT_PARQUET = 'structure_daily.parquet'
SIGNALS_PARQUET = 'signals_runpoint.parquet'
MANIFEST_NAME = 'manifest.json'


def _cfg_dict(cfg: Any, dc_type: type) -> dict[str, Any]:
    if is_dataclass(cfg):
        return asdict(cfg)
    out: dict[str, Any] = {}
    for f in fields(dc_type):
        out[f.name] = getattr(cfg, f.name, f.default)
    return out


def _file_snapshot(data_portal: DataPortal, filename: str) -> dict[str, Any]:
    """数据文件快照（审计用）：size + 末 bar 时间，不含 mtime。"""
    path = Path(data_portal.base_dir) / filename
    if not path.exists():
        return {'path': str(path.resolve()), 'exists': False, 'size': 0, 'last_datetime': None}
    st = path.stat()
    last_dt: Any = None
    try:
        df = data_portal.load_frame(filename)
        if not df.empty:
            col = 'datetime' if 'datetime' in df.columns else (
                '交易时间' if '交易时间' in df.columns else None
            )
            if col:
                last_dt = pd.to_datetime(df[col].max())
    except Exception:
        pass
    return {
        'path': str(path.resolve()),
        'exists': True,
        'size': int(st.st_size),
        'last_datetime': _json_default(last_dt) if last_dt is not None else None,
    }


def _json_default(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if hasattr(obj, 'item'):
        try:
            return obj.item()
        except Exception:
            pass
    raise TypeError(f'不可 JSON 序列化: {type(obj)}')


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=_json_default, sort_keys=True)


def _loads(s: Any) -> Any:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    if not isinstance(s, str) or not s.strip():
        return None
    return json.loads(s)


def _parse_iso_ts(value: Any) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value
    if isinstance(value, datetime):
        return pd.Timestamp(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return pd.Timestamp(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return pd.Timestamp(datetime.fromisoformat(s))
    except Exception:
        return pd.to_datetime(value, errors='coerce')


def _parse_close_index(raw: list[Any]) -> list[pd.Timestamp]:
    if not raw:
        return []
    out: list[pd.Timestamp] = []
    for x in raw:
        ts = _parse_iso_ts(x)
        if ts is not None and not pd.isna(ts):
            out.append(ts)
    return out


def _parse_close_values(raw: list[Any]) -> list[float]:
    if not raw:
        return []
    return [float(x) for x in raw]


def _struct_cfg(cfg: Any) -> StructCfg:
    base = StructCfg()
    if is_dataclass(cfg):
        data = asdict(cfg) if isinstance(cfg, StructCfg) else {**asdict(base), **_cfg_dict(cfg, StructCfg)}
    else:
        data = _cfg_dict(cfg, StructCfg)
    return StructCfg(**{f.name: data.get(f.name, getattr(base, f.name)) for f in fields(StructCfg)})


def _macd_cfg(cfg: Any) -> MacdDivCfg:
    base = MacdDivCfg()
    if is_dataclass(cfg):
        data = asdict(cfg) if isinstance(cfg, MacdDivCfg) else {**asdict(base), **_cfg_dict(cfg, MacdDivCfg)}
    else:
        data = _cfg_dict(cfg, MacdDivCfg)
    return MacdDivCfg(**{f.name: data.get(f.name, getattr(base, f.name)) for f in fields(MacdDivCfg)})


# build_identity 方法说明
# 输入参数：data_portal、struct_cfg、macd_cfg；可选 daily/30m/1m 文件名
# 输出参数：dict（version、struct_cfg、macd_cfg、data_files 快照、lookback_bars）
# 用途：生成特征表身份指纹，供 find/resolve 匹配
def build_identity(
    data_portal: DataPortal,
    struct_cfg: StructCfg,
    macd_cfg: MacdDivCfg,
    *,
    daily_file: str = DEFAULT_DAILY_FILE,
    file_30m: str = DEFAULT_30M_FILE,
    file_1m: str = DEFAULT_1M_FILE,
) -> dict[str, Any]:
    data_files = {
        'daily': _file_snapshot(data_portal, daily_file),
        'm30': _file_snapshot(data_portal, file_30m),
        'm1': _file_snapshot(data_portal, file_1m),
    }
    return {
        'version': FEATURE_SIGNAL_VERSION,
        'signal_version': FEATURE_SIGNAL_VERSION,
        'signal_logic': '1457_1m_exact_or_30m_fallback',
        'struct_cfg': asdict(struct_cfg),
        'macd_cfg': asdict(macd_cfg),
        'data_files': data_files,
        'daily_file': daily_file,
        'file_30m': file_30m,
        'file_1m': file_1m,
        'lookback_bars': DEFAULT_LOOKBACK_BARS,
    }


# identity_bundle_id 方法说明
# 输入参数：identity（build_identity 返回值）
# 输出参数：str，16 位 hex 目录名
# 用途：按结构/MACD 参数隔离特征表目录（不含 mtime）
def identity_bundle_id(identity: dict[str, Any]) -> str:
    payload = _dumps({
        'struct_cfg': identity['struct_cfg'],
        'macd_cfg': identity['macd_cfg'],
        'signal_version': identity.get('signal_version', 1),
    })
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _identity_matches(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (
        a.get('struct_cfg') == b.get('struct_cfg')
        and a.get('macd_cfg') == b.get('macd_cfg')
        and a.get('signal_version', 1) == b.get('signal_version', 1)
    )


def _manifest_trade_days(manifest: dict[str, Any]) -> int:
    try:
        return int(manifest.get('trade_days', 0) or 0)
    except (TypeError, ValueError):
        return 0


# find_feature_store_dir 方法说明
# 输入参数：features_root、identity
# 输出参数：Path | None，匹配目录；多候选时取 trade_days 最多者
# 用途：按参数查找已有特征表，兼容历史 bundle_id 目录名
def find_feature_store_dir(features_root: Path, identity: dict[str, Any]) -> Optional[Path]:
    bundle_id = identity_bundle_id(identity)
    exact = features_root / bundle_id
    candidates: list[Path] = []

    def _try_add(path: Path) -> None:
        if not (path / MANIFEST_NAME).exists():
            return
        if not (path / STRUCT_PARQUET).exists() or not (path / SIGNALS_PARQUET).exists():
            return
        try:
            manifest = json.loads((path / MANIFEST_NAME).read_text(encoding='utf-8'))
        except Exception:
            return
        if _identity_matches(manifest.get('identity', {}), identity):
            candidates.append(path)

    _try_add(exact)
    if features_root.exists():
        for child in sorted(features_root.iterdir()):
            if child.is_dir() and child != exact:
                _try_add(child)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    def _score(path: Path) -> tuple[int, str]:
        try:
            manifest = json.loads((path / MANIFEST_NAME).read_text(encoding='utf-8'))
            days = _manifest_trade_days(manifest)
            end_d = str(manifest.get('trade_date_end') or '')
            return days, end_d
        except Exception:
            return 0, ''

    return max(candidates, key=_score)


def _compute_signal_row(
    data_portal: DataPortal,
    macd_cfg: MacdDivCfg,
    as_of: str,
    time_str: str,
    struct_row: Optional[dict[str, Any]],
    *,
    file_30m: str,
    file_1m: str,
    lookback_bars: int,
) -> dict[str, Any]:
    trade_date = pd.to_datetime(as_of).date()
    dt_str = f"{trade_date.isoformat()} {time_str}:00"

    div: dict[str, Any] = {'has_top': False, 'has_bottom': False, 'top_detail': None, 'bottom_detail': None}
    current_price: Optional[float] = None
    bar_time = None
    close_index: list[Any] = []
    close_values: list[float] = []
    price_source: Optional[str] = None

    df = data_portal.get_index_bars(file_30m, as_of, count=lookback_bars)
    if df is not None and not df.empty and 'close' in df.columns:
        close = df['close'].astype(float)
        if 'datetime' in df.columns:
            close.index = pd.to_datetime(df['datetime'])
        if len(close):
            macd_df = macd_from_close(close, macd_cfg)
            div = DivergenceDetector(macd_cfg).detect(close, macd_df)
            current_price = float(close.iloc[-1])
            bar_time = close.index[-1]
            close_index = [_json_default(t) for t in close.index]
            close_values = [float(x) for x in close.values]
            price_source = '30m'

    if time_str == '14:57':
        px_1m = data_portal.get_index_1m_close_at_1457(file_1m, dt_str)
        if px_1m is not None:
            current_price = float(px_1m)
            bar_time = pd.to_datetime(dt_str)
            price_source = '1m_1457'
        elif current_price is not None:
            price_source = '30m_1457_fallback'

    has_1m_top = False if time_str == '14:57' else detect_1m_top_divergence(
        data_portal,
        as_of,
        macd_cfg,
        index_1m_file=file_1m,
        lookback_bars=lookback_bars,
    )

    rh = struct_row.get('range_high') if struct_row else None
    rl = struct_row.get('range_low') if struct_row else None
    break_up = break_down = in_range = None
    if current_price is not None and rh is not None and rl is not None:
        px = float(current_price)
        rh_f = float(rh)
        rl_f = float(rl)
        break_up = px >= rh_f * 1.01
        break_down = px <= rl_f * 0.99
        in_range = (px > rl_f) and (px < rh_f)

    return {
        'datetime': dt_str,
        'trade_date': trade_date.isoformat(),
        'time_str': time_str,
        'current_price': current_price,
        'bar_time': _json_default(bar_time) if bar_time is not None else None,
        'has_top': bool(div.get('has_top')),
        'has_bottom': bool(div.get('has_bottom')),
        'top_detail_json': _dumps(div.get('top_detail')) if div.get('top_detail') else None,
        'bottom_detail_json': _dumps(div.get('bottom_detail')) if div.get('bottom_detail') else None,
        'close_index_json': _dumps(close_index),
        'close_values_json': _dumps(close_values),
        'has_1m_top': bool(has_1m_top),
        'price_source': price_source,
        'break_up': break_up,
        'break_down': break_down,
        'in_range': in_range,
        'range_high': rh,
        'range_low': rl,
    }


def _bundle_to_structure_row(trade_date: date, bundle: dict[str, Any]) -> dict[str, Any]:
    center = bundle.get('center')
    zg = float(center['ZG']) if center else None
    zd = float(center['ZD']) if center else None
    return {
        'trade_date': trade_date.isoformat(),
        'ZG': zg,
        'ZD': zd,
        'range_high': bundle.get('range_high'),
        'range_low': bundle.get('range_low'),
        'range_pct': bundle.get('range_pct'),
        'range_state': bool(bundle.get('range_state')),
        'extend_cnt': int(bundle.get('extend_cnt', 0) or 0),
        'req_end_date': _json_default(bundle.get('req_end_date')),
        'last_trade_date': _json_default(bundle.get('last_trade_date')),
        'last_close': bundle.get('last_close'),
        'range_high_dt': _json_default(bundle.get('range_high_dt')),
        'range_low_dt': _json_default(bundle.get('range_low_dt')),
        'last_processed_stroke_dt': _json_default(bundle.get('last_processed_stroke_dt')),
        'center_json': _dumps(center) if center else None,
        'current_strokes_json': _dumps(bundle.get('current_strokes') or []),
    }


def _rows_for_trade_dates(
    data_portal: DataPortal,
    struct_cfg: StructCfg,
    macd_cfg: MacdDivCfg,
    daily_df: pd.DataFrame,
    trade_dates: list[date],
    *,
    file_30m: str,
    file_1m: str,
    lookback_bars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    structure_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    for td in trade_dates:
        as_of_9 = f"{td.isoformat()} 09:00:00"
        bundle = build_daily_bundle_from_daily_df(
            daily_df, struct_cfg, as_of_9, update_cache=False,
        )
        srow = _bundle_to_structure_row(td, bundle)
        structure_rows.append(srow)
        for time_str in SIGNAL_TIME_STRS:
            as_of = f"{td.isoformat()} {time_str}:00"
            signal_rows.append(
                _compute_signal_row(
                    data_portal,
                    macd_cfg,
                    as_of,
                    time_str,
                    srow,
                    file_30m=file_30m,
                    file_1m=file_1m,
                    lookback_bars=lookback_bars,
                )
            )
    return structure_rows, signal_rows


# extend_feature_store 方法说明
# 输入参数：data_portal、struct_cfg、macd_cfg、store_dir、待追加 trade_dates
# 输出参数：Path，更新后的 store_dir
# 用途：向已有特征表增量追加缺失交易日（merge parquet，不新建参数版本）
def extend_feature_store(
    data_portal: DataPortal,
    struct_cfg: StructCfg,
    macd_cfg: MacdDivCfg,
    store_dir: Path,
    trade_dates: list[date],
    *,
    daily_file: str = DEFAULT_DAILY_FILE,
    file_30m: str = DEFAULT_30M_FILE,
    file_1m: str = DEFAULT_1M_FILE,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
) -> Path:
    """向已有特征表追加缺失交易日（同 bundle_id 目录，不新建参数版本）。"""
    store_dir = Path(store_dir)
    manifest_path = store_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f'缺少 manifest: {manifest_path}')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

    daily_df = data_portal.load_frame(daily_file)
    if daily_df.empty:
        raise RuntimeError(f'日线数据为空: {daily_file}')

    structure_rows, signal_rows = _rows_for_trade_dates(
        data_portal,
        struct_cfg,
        macd_cfg,
        daily_df,
        trade_dates,
        file_30m=file_30m,
        file_1m=file_1m,
        lookback_bars=lookback_bars,
    )

    struct_path = store_dir / STRUCT_PARQUET
    sig_path = store_dir / SIGNALS_PARQUET
    old_struct = pd.read_parquet(struct_path) if struct_path.exists() else pd.DataFrame()
    old_sig = pd.read_parquet(sig_path) if sig_path.exists() else pd.DataFrame()

    struct_df = pd.concat([old_struct, pd.DataFrame(structure_rows)], ignore_index=True)
    struct_df = struct_df.drop_duplicates(subset=['trade_date'], keep='last').sort_values('trade_date')
    sig_df = pd.concat([old_sig, pd.DataFrame(signal_rows)], ignore_index=True)
    sig_df = sig_df.drop_duplicates(subset=['datetime'], keep='last').sort_values('datetime')

    struct_df.to_parquet(struct_path, index=False)
    sig_df.to_parquet(sig_path, index=False)

    all_dates = sorted({str(x) for x in struct_df['trade_date'].tolist()})
    manifest['extended_at'] = datetime.now().isoformat(timespec='seconds')
    manifest['trade_date_start'] = all_dates[0] if all_dates else manifest.get('trade_date_start')
    manifest['trade_date_end'] = all_dates[-1] if all_dates else manifest.get('trade_date_end')
    manifest['trade_days'] = len(all_dates)
    manifest['signal_rows'] = int(len(sig_df))
    ident = dict(manifest.get('identity') or {})
    ident['data_files'] = {
        'daily': _file_snapshot(data_portal, daily_file),
        'm30': _file_snapshot(data_portal, file_30m),
        'm1': _file_snapshot(data_portal, file_1m),
    }
    manifest['identity'] = ident
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return store_dir


# build_feature_store 方法说明
# 输入参数：data_portal、struct_cfg、macd_cfg、trade_dates、features_root
# 输出参数：Path，新建目录路径（含 manifest + 两套 parquet）
# 用途：按请求区间全新构建特征表（--rebuild 或无匹配表时）
def build_feature_store(
    data_portal: DataPortal,
    struct_cfg: StructCfg,
    macd_cfg: MacdDivCfg,
    trade_dates: list[date],
    features_root: Path,
    *,
    daily_file: str = DEFAULT_DAILY_FILE,
    file_30m: str = DEFAULT_30M_FILE,
    file_1m: str = DEFAULT_1M_FILE,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
) -> Path:
    identity = build_identity(
        data_portal, struct_cfg, macd_cfg,
        daily_file=daily_file, file_30m=file_30m, file_1m=file_1m,
    )
    bundle_id = identity_bundle_id(identity)
    out_dir = features_root / bundle_id
    out_dir.mkdir(parents=True, exist_ok=True)

    daily_df = data_portal.load_frame(daily_file)
    if daily_df.empty:
        raise RuntimeError(f'日线数据为空: {daily_file}')

    structure_rows, signal_rows = _rows_for_trade_dates(
        data_portal,
        struct_cfg,
        macd_cfg,
        daily_df,
        trade_dates,
        file_30m=file_30m,
        file_1m=file_1m,
        lookback_bars=lookback_bars,
    )

    pd.DataFrame(structure_rows).to_parquet(out_dir / STRUCT_PARQUET, index=False)
    pd.DataFrame(signal_rows).to_parquet(out_dir / SIGNALS_PARQUET, index=False)

    start_d = min(trade_dates).isoformat() if trade_dates else None
    end_d = max(trade_dates).isoformat() if trade_dates else None
    manifest = {
        'bundle_id': bundle_id,
        'identity': identity,
        'built_at': datetime.now().isoformat(timespec='seconds'),
        'trade_date_start': start_d,
        'trade_date_end': end_d,
        'trade_days': len(trade_dates),
        'signal_rows': len(signal_rows),
        'files': {
            'structure_daily': STRUCT_PARQUET,
            'signals_runpoint': SIGNALS_PARQUET,
        },
    }
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return out_dir


class FeatureStore:
    # 模块说明
    # 输入参数：store_dir（特征表目录）、manifest（manifest.json 内容）
    # 输出参数：FeatureStore 实例（内存 dict 索引）
    # 用途：回测按 trade_date / (trade_date, time_str) lookup 结构与 MACD 信号
    """内存索引的特征表，供回测按日期/时点 lookup。"""

    def __init__(self, store_dir: Path, manifest: dict[str, Any]):
        self.store_dir = Path(store_dir)
        self.manifest = manifest
        self.identity = manifest.get('identity') or {}
        struct_df = pd.read_parquet(self.store_dir / STRUCT_PARQUET)
        sig_df = pd.read_parquet(self.store_dir / SIGNALS_PARQUET)
        self._structure: dict[str, dict[str, Any]] = {
            str(r['trade_date']): dict(r) for r in struct_df.to_dict(orient='records')
        }
        self._signals: dict[str, dict[str, Any]] = {}
        for r in sig_df.to_dict(orient='records'):
            key = f"{r['trade_date']} {r['time_str']}"
            self._signals[key] = dict(r)
        self._parsed_signal_cache: dict[str, dict[str, Any]] = {}

    def _materialize_signal(self, key: str, row: dict[str, Any]) -> dict[str, Any]:
        cached = self._parsed_signal_cache.get(key)
        if cached is not None:
            return cached
        top_detail = _loads(row.get('top_detail_json'))
        bottom_detail = _loads(row.get('bottom_detail_json'))
        close_index = _parse_close_index(_loads(row.get('close_index_json')) or [])
        close_values = _parse_close_values(_loads(row.get('close_values_json')) or [])
        bar_time = _parse_iso_ts(row.get('bar_time'))
        parsed = {
            'divergence': {
                'has_top': bool(row.get('has_top')),
                'has_bottom': bool(row.get('has_bottom')),
                'top_detail': top_detail,
                'bottom_detail': bottom_detail,
            },
            'current_price': row.get('current_price'),
            'timestamp': row.get('datetime'),
            'bar_time': bar_time,
            'close_index': close_index,
            'close_values': close_values,
            'has_1m_top': bool(row.get('has_1m_top')),
            'macd_mode': 'feature_store',
        }
        self._parsed_signal_cache[key] = parsed
        return parsed

    @property
    def bundle_id(self) -> str:
        return str(self.manifest.get('bundle_id') or self.store_dir.name)

    # covers_dates 方法说明
    # 输入参数：trade_dates
    # 输出参数：bool，是否全部覆盖（含各 SIGNAL_TIME_STRS）
    # 用途：resolve 时判断是否可直接加载
    def covers_dates(self, trade_dates: list[date]) -> bool:
        return not self.missing_trade_dates(trade_dates)

    # missing_trade_dates 方法说明
    # 输入参数：trade_dates
    # 输出参数：list[date]，缺失的交易日列表
    # 用途：决定 extend 哪些日期
    def missing_trade_dates(self, trade_dates: list[date]) -> list[date]:
        if not trade_dates:
            return []
        missing: list[date] = []
        for d in trade_dates:
            key = d.isoformat()
            if key not in self._structure:
                missing.append(d)
                continue
            for t in SIGNAL_TIME_STRS:
                if f"{key} {t}" not in self._signals:
                    missing.append(d)
                    break
        return missing

    # get_structure_bundle 方法说明
    # 输入参数：trade_date
    # 输出参数：dict | None（center、range、strokes 等，含 intraday_frozen）
    # 用途：9:00 before_market_open 读取日线结构快照（截止昨收）
    def get_structure_bundle(self, trade_date: date | str) -> Optional[dict[str, Any]]:
        key = pd.to_datetime(trade_date).date().isoformat()
        row = self._structure.get(key)
        if not row:
            return None
        center = _loads(row.get('center_json'))
        current_strokes = _loads(row.get('current_strokes_json')) or []
        return {
            'structure_mode': 'feature_store',
            'structure_asof': 'prev_trade_day_close',
            'intraday_frozen': True,
            'last_processed_stroke_dt': row.get('last_processed_stroke_dt'),
            'req_end_date': row.get('req_end_date'),
            'last_trade_date': row.get('last_trade_date'),
            'last_close': row.get('last_close'),
            'center': center,
            'range_high': row.get('range_high'),
            'range_low': row.get('range_low'),
            'range_high_dt': row.get('range_high_dt'),
            'range_low_dt': row.get('range_low_dt'),
            'range_pct': row.get('range_pct'),
            'extend_cnt': int(row.get('extend_cnt', 0) or 0),
            'range_state': bool(row.get('range_state')),
            'current_strokes': current_strokes,
        }

    # get_div_info 方法说明
    # 输入参数：as_of（运行点时间）、time_str（可选，如 10:00）
    # 输出参数：dict | None（divergence、current_price、has_1m_top、macd_mode=feature_store）
    # 用途：运行点 MACD/1m 顶背离 lookup
    def get_div_info(self, as_of: str, time_str: Optional[str] = None) -> Optional[dict[str, Any]]:
        ts = pd.to_datetime(as_of)
        d = ts.date().isoformat()
        t = time_str or ts.strftime('%H:%M')
        key = f"{d} {t}"
        row = self._signals.get(key)
        if not row:
            return None
        return self._materialize_signal(key, row)


# resolve_feature_store 方法说明
# 输入参数：data_portal、cfg（含 Struct/Macd 参数）、trade_dates；可选 features_root、rebuild
# 输出参数：FeatureStore 实例（加载 / extend / 新建）
# 用途：回测启动时解析特征表：命中加载，缺日 extend，无表则 build
def resolve_feature_store(
    data_portal: DataPortal,
    cfg: Any,
    trade_dates: list[date],
    *,
    features_root: Path | str | None = None,
    daily_file: str = DEFAULT_DAILY_FILE,
    file_30m: str = DEFAULT_30M_FILE,
    file_1m: str = DEFAULT_1M_FILE,
    rebuild: bool = False,
) -> FeatureStore:
    """按 struct/macd/数据指纹查找特征表。

    - 已有表且覆盖回测区间 → 直接加载
    - 已有表但缺部分交易日 → 同目录增量追加（不新建参数版本）
    - 无表或 rebuild=True → 新建目录并构建（仅含本次请求的 trade_dates）
    """
    struct_cfg = _struct_cfg(cfg)
    macd_cfg = _macd_cfg(cfg)
    root = Path(features_root) if features_root else Path(data_portal.base_dir) / DEFAULT_FEATURES_ROOT
    root.mkdir(parents=True, exist_ok=True)
    identity = build_identity(
        data_portal, struct_cfg, macd_cfg,
        daily_file=daily_file, file_30m=file_30m, file_1m=file_1m,
    )

    store_dir: Optional[Path] = None
    if not rebuild:
        store_dir = find_feature_store_dir(root, identity)
        if store_dir is not None:
            manifest = json.loads((store_dir / MANIFEST_NAME).read_text(encoding='utf-8'))
            store = FeatureStore(store_dir, manifest)
            if store.covers_dates(trade_dates):
                return store
            missing = store.missing_trade_dates(trade_dates)
            if missing:
                extend_feature_store(
                    data_portal,
                    struct_cfg,
                    macd_cfg,
                    store_dir,
                    missing,
                    daily_file=daily_file,
                    file_30m=file_30m,
                    file_1m=file_1m,
                )
                manifest = json.loads((store_dir / MANIFEST_NAME).read_text(encoding='utf-8'))
                return FeatureStore(store_dir, manifest)

    store_dir = build_feature_store(
        data_portal, struct_cfg, macd_cfg, trade_dates, root,
        daily_file=daily_file, file_30m=file_30m, file_1m=file_1m,
    )
    manifest = json.loads((store_dir / MANIFEST_NAME).read_text(encoding='utf-8'))
    return FeatureStore(store_dir, manifest)
