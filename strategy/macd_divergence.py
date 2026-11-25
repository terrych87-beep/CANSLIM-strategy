"""MACD 背离检测与增量会话模块。

输入参数：
  - DataPortal、MacdDivCfg、运行点时间 as_of、30m/1m close 序列
输出参数：
  - divergence dict（has_top/has_bottom/top_detail/bottom_detail）
  - IncrementalMacdSession.check() 返回完整 div_info（含 has_1m_top）
用途：
  - 30m MACD 顶底背离判定、震荡等待态计数更新、1m 顶背离止盈

使用说明：
  - 回测有特征表时优先 lookup；miss 时 MACDDivergenceModule 走 IncrementalMacdSession
  - 参数权威默认值见 MacdDivCfg；与聚宽差异见 `.cursor/rules/local-macd-reconciliation-baseline.mdc`
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from typing import Any, Dict, Optional

import pandas as pd

from engine.data_portal import DataPortal


@dataclass
class DivergenceResult:
    has_top: bool
    has_bottom: bool
    top_detail: Optional[dict]
    bottom_detail: Optional[dict]


# MacdDivCfg 说明
# 输入参数：各 MACD/背离阈值字段（见 dataclass 默认值）
# 输出参数：MacdDivCfg 实例
# 用途：统一 MACD 快/慢/信号线、柱带长度、前序柱比较等本地权威参数
@dataclass
class MacdDivCfg:
    """本地权威 MACD/背离参数。与聚宽 StrategyConfig 差异见 `.cursor/rules/local-macd-reconciliation-baseline.mdc`"""
    MACD_FAST: int = 12
    MACD_SLOW: int = 26
    MACD_SIGNAL: int = 9
    MACD_ZERO_EPS: float = 1e-5
    MACD_RATIO: float = 1.0
    MIN_BAND_LEN: int = 3  # 聚宽 txt 为 5；本地口径 intentionally 更松
    MIN_PRICE_MOVE: float = 0.0  # 聚宽 txt 为 0.0005
    PREV_COMPARE_MIN_BAND_LEN: int = 4  # 聚宽原代码无此筛；本地前序同向柱须长度 >= 4


# macd_from_close 方法说明
# 输入参数：close（收盘价序列）、cfg（MacdDivCfg）
# 输出参数：DataFrame（dif、dea、hist）
# 用途：由收盘价计算 MACD 三线
def macd_from_close(close: pd.Series, cfg: MacdDivCfg) -> pd.DataFrame:
    close = close.astype(float)
    ema_fast = close.ewm(span=cfg.MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=cfg.MACD_SLOW, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=cfg.MACD_SIGNAL, adjust=False).mean()
    hist = 2 * (dif - dea)
    return pd.DataFrame({'dif': dif, 'dea': dea, 'hist': hist})


class DivergenceDetector:
    # 模块说明
    # 输入参数：cfg（MacdDivCfg）
    # 输出参数：DivergenceDetector 实例
    # 用途：由 close + MACD hist 构建同向柱带并检测顶/底背离
    def __init__(self, cfg: MacdDivCfg):
        self.cfg = cfg

    def build_bands(self, close: pd.Series, hist: pd.Series):
        bands = []
        band = None
        for i in range(len(close)):
            price = float(close.iloc[i])
            h = float(hist.iloc[i])
            sign = 0 if abs(h) < self.cfg.MACD_ZERO_EPS else (1 if h > 0 else -1)
            if band is None:
                if sign == 0:
                    continue
                band = {'sign': sign, 'start_idx': i, 'end_idx': i, 'max_price': price, 'min_price': price, 'max_idx': i, 'min_idx': i, 'max_hist': h, 'min_hist': h, 'hist_values': [h]}
                continue
            if sign == 0 or sign == band['sign']:
                band['end_idx'] = i
                if price >= band['max_price']:
                    band['max_price'] = price
                    band['max_idx'] = i
                if price <= band['min_price']:
                    band['min_price'] = price
                    band['min_idx'] = i
                band['max_hist'] = max(float(band['max_hist']), h)
                band['min_hist'] = min(float(band['min_hist']), h)
                band['hist_values'].append(h)
            else:
                if band['end_idx'] - band['start_idx'] + 1 >= self.cfg.MIN_BAND_LEN:
                    bands.append(band)
                band = {'sign': sign, 'start_idx': i, 'end_idx': i, 'max_price': price, 'min_price': price, 'max_idx': i, 'min_idx': i, 'max_hist': h, 'min_hist': h, 'hist_values': [h]}
        if band and band['end_idx'] - band['start_idx'] + 1 >= self.cfg.MIN_BAND_LEN:
            bands.append(band)
        return bands

    @staticmethod
    def _two_step_fall(vals):
        return vals is not None and len(vals) >= 3 and vals[-3] > vals[-2] > vals[-1]

    @staticmethod
    def _two_step_rise(vals):
        return vals is not None and len(vals) >= 3 and vals[-3] < vals[-2] < vals[-1]

    # detect 方法说明
    # 输入参数：close、macd_df（macd_from_close 输出）
    # 输出参数：dict（has_top、has_bottom、top_detail、bottom_detail）
    # 用途：判定当前是否形成顶背离或底背离
    def detect(self, close: pd.Series, macd_df: pd.DataFrame) -> Dict[str, Any]:
        res: Dict[str, Any] = {'has_top': False, 'has_bottom': False, 'top_detail': None, 'bottom_detail': None}
        hist = macd_df['hist']
        bands = self.build_bands(close, hist)
        if len(bands) < 2:
            return res
        idx_to_time = close.index
        curr = bands[-1]

        def t(i):
            try:
                return idx_to_time[int(i)]
            except Exception:
                return None

        if curr.get('sign') == 1:
            prev = next(
                (b for b in reversed(bands[:-1]) if b.get('sign') == 1 and (int(b.get('end_idx', -1)) - int(b.get('start_idx', 0)) + 1) >= int(self.cfg.PREV_COMPARE_MIN_BAND_LEN)),
                None,
            )
            if prev:
                price_ok = float(curr['max_price']) > float(prev['max_price']) * (1 + self.cfg.MIN_PRICE_MOVE)
                macd_ok = float(curr['max_hist']) < float(prev['max_hist']) * self.cfg.MACD_RATIO
                fall_ok = self._two_step_fall(curr.get('hist_values'))
                if price_ok and macd_ok and fall_ok:
                    cs = t(curr.get('start_idx'))
                    uid = f"TOP_{pd.to_datetime(cs).strftime('%Y%m%d%H%M')}" if cs is not None else None
                    res['has_top'] = True
                    res['top_detail'] = {'curr_band_start_time': t(curr.get('start_idx')), 'curr_band_end_time': t(curr.get('end_idx')), 'curr_max_price': curr['max_price'], 'prev_max_price': prev['max_price'], 'curr_max_hist': curr['max_hist'], 'prev_max_hist': prev['max_hist'], 'curr_band_uid': uid}
        if curr.get('sign') == -1:
            prev = next(
                (b for b in reversed(bands[:-1]) if b.get('sign') == -1 and (int(b.get('end_idx', -1)) - int(b.get('start_idx', 0)) + 1) >= int(self.cfg.PREV_COMPARE_MIN_BAND_LEN)),
                None,
            )
            if prev:
                price_ok = float(curr['min_price']) < float(prev['min_price']) * (1 - self.cfg.MIN_PRICE_MOVE)
                macd_ok = float(curr['min_hist']) > float(prev['min_hist']) * self.cfg.MACD_RATIO
                rise_ok = self._two_step_rise(curr.get('hist_values'))
                if price_ok and macd_ok and rise_ok:
                    cs = t(curr.get('start_idx'))
                    uid = f"BOT_{pd.to_datetime(cs).strftime('%Y%m%d%H%M')}" if cs is not None else None
                    res['has_bottom'] = True
                    res['bottom_detail'] = {'curr_band_start_time': t(curr.get('start_idx')), 'curr_band_end_time': t(curr.get('end_idx')), 'curr_min_price': curr['min_price'], 'prev_min_price': prev['min_price'], 'curr_min_hist': curr['min_hist'], 'prev_min_hist': prev['min_hist'], 'curr_band_uid': uid}
        return res


def _dt_to_str(x) -> str:
    if x is None:
        return ''
    if hasattr(x, 'strftime'):
        return x.strftime('%Y-%m-%d %H:%M:%S')
    try:
        return pd.to_datetime(x).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(x)


def _normalize_osc_rules_model(raw: Any, default: str) -> str:
    m = str(raw if raw is not None and str(raw).strip() else default).strip().lower()
    return 'v1' if m == 'v1' else 'v2'


def osc_rules_model_long(cfg: Any = None) -> str:
    if cfg is None:
        return 'v1'
    explicit = getattr(cfg, 'OSC_RULES_MODEL_LONG', None)
    if explicit is not None and str(explicit).strip():
        return _normalize_osc_rules_model(explicit, 'v1')
    legacy = getattr(cfg, 'OSC_RULES_MODEL', None)
    if legacy is not None and str(legacy).strip():
        return _normalize_osc_rules_model(legacy, 'v1')
    return 'v1'


def osc_rules_model_short(cfg: Any = None) -> str:
    if cfg is None:
        return 'v2'
    explicit = getattr(cfg, 'OSC_RULES_MODEL_SHORT', None)
    if explicit is not None and str(explicit).strip():
        return _normalize_osc_rules_model(explicit, 'v2')
    legacy = getattr(cfg, 'OSC_RULES_MODEL', None)
    if legacy is not None and str(legacy).strip():
        return _normalize_osc_rules_model(legacy, 'v2')
    return 'v2'


def osc_rules_model(cfg: Any = None) -> str:
    """兼容旧调用：等同震荡多模型。"""
    return osc_rules_model_long(cfg)


# update_counts_on_divergence 方法说明
# 输入参数：div_info（check 返回值）、st（状态字典）、cfg（震荡模型参数）
# 输出参数：无（原地更新 st 的背离计数与 osc_wait）
# 用途：运行点检测到背离后更新 top/bottom 计数与震荡等待态
def update_counts_on_divergence(div_info: Dict[str, Any], st: dict, cfg: Any = None) -> None:
    if not div_info or not div_info.get('divergence'):
        return
    div = div_info['divergence']
    cur_close = div_info.get('current_price')
    bar_time = div_info.get('bar_time')
    if div.get('has_top') and div.get('has_bottom'):
        return
    st.setdefault('bottom_divergence_count', 0)
    st.setdefault('top_divergence_count', 0)
    st.setdefault('last_counted_top_band_uid', None)
    st.setdefault('last_counted_bottom_band_uid', None)
    st.setdefault('last_bottom_band_low_close', None)
    st.setdefault('last_top_band_high_close', None)
    st.setdefault('last_bottom_band_start_time', None)
    st.setdefault('last_bottom_band_end_time', None)
    st.setdefault('last_bottom_band_min_hist', None)
    st.setdefault('last_top_band_start_time', None)
    st.setdefault('last_top_band_end_time', None)
    st.setdefault('last_top_band_max_hist', None)
    if div.get('has_bottom'):
        bd = div.get('bottom_detail') or {}
        uid = bd.get('curr_band_uid')
        if uid and st.get('last_counted_bottom_band_uid') != uid:
            y_prev = st.get('last_bottom_band_low_close')
            if y_prev is None:
                st['bottom_divergence_count'] = 1
            else:
                if cur_close is not None and float(cur_close) < float(y_prev):
                    st['bottom_divergence_count'] = int(st.get('bottom_divergence_count', 0) or 0) + 1
                else:
                    st['bottom_divergence_count'] = 1
            st['top_divergence_count'] = 0
            st['last_counted_bottom_band_uid'] = uid
            st['last_bottom_band_low_close'] = bd.get('curr_min_price')
            st['last_bottom_band_start_time'] = bd.get('curr_band_start_time')
            st['last_bottom_band_end_time'] = bd.get('curr_band_end_time')
            st['last_bottom_band_min_hist'] = bd.get('curr_min_hist')
        if cur_close is not None and bar_time is not None:
            ow = st.get('osc_wait') or {}
            if osc_rules_model_long(cfg) == 'v1':
                ow['long'] = {
                    'active': True,
                    'start_bar_time': _dt_to_str(bar_time),
                    'ref_close': float(cur_close),
                    'ref_band_uid': uid,
                }
                ow['short'] = {'active': False, 'start_bar_time': None, 'ref_close': None, 'ref_band_uid': None}
            else:
                ref_low = bd.get('curr_min_price')
                if ref_low is not None:
                    ow['long'] = {
                        'active': True,
                        'start_bar_time': _dt_to_str(bar_time),
                        'ref_low': float(ref_low),
                        'ref_band_uid': uid,
                    }
                    ow['short'] = {'active': False, 'start_bar_time': None, 'ref_high': None, 'ref_band_uid': None}
            st['osc_wait'] = ow
    if div.get('has_top'):
        td = div.get('top_detail') or {}
        uid = td.get('curr_band_uid')
        if uid and st.get('last_counted_top_band_uid') != uid:
            z_prev = st.get('last_top_band_high_close')
            if z_prev is None:
                st['top_divergence_count'] = 1
            else:
                if cur_close is not None and float(cur_close) > float(z_prev):
                    st['top_divergence_count'] = int(st.get('top_divergence_count', 0) or 0) + 1
                else:
                    st['top_divergence_count'] = 1
            st['bottom_divergence_count'] = 0
            st['last_counted_top_band_uid'] = uid
            st['last_top_band_high_close'] = td.get('curr_max_price')
            st['last_top_band_start_time'] = td.get('curr_band_start_time')
            st['last_top_band_end_time'] = td.get('curr_band_end_time')
            st['last_top_band_max_hist'] = td.get('curr_max_hist')
        if cur_close is not None and bar_time is not None:
            ow = st.get('osc_wait') or {}
            if osc_rules_model_short(cfg) == 'v1':
                ow['short'] = {
                    'active': True,
                    'start_bar_time': _dt_to_str(bar_time),
                    'ref_close': float(cur_close),
                    'ref_band_uid': uid,
                }
                ow['long'] = {'active': False, 'start_bar_time': None, 'ref_close': None, 'ref_band_uid': None}
            else:
                ref_high = td.get('curr_max_price')
                if ref_high is not None:
                    ow['short'] = {
                        'active': True,
                        'start_bar_time': _dt_to_str(bar_time),
                        'ref_high': float(ref_high),
                        'ref_band_uid': uid,
                    }
                    ow['long'] = {'active': False, 'start_bar_time': None, 'ref_low': None, 'ref_band_uid': None}
            st['osc_wait'] = ow


# detect_1m_top_divergence 方法说明
# 输入参数：data_portal、end_dt（截止时点）、cfg；可选 1m 文件名与 lookback
# 输出参数：bool，是否存在 1m 顶背离
# 用途：震荡突破多单止盈等分支的 1m 顶背离判断
def detect_1m_top_divergence(
    data_portal: DataPortal,
    end_dt,
    cfg: MacdDivCfg | None = None,
    *,
    index_1m_file: str = '上证指数1分钟加入实时数据.csv',
    lookback_bars: int = 260,
) -> bool:
    """1 分钟 MACD 顶背离检测（与 trading_conditions._detect_1m_top_divergence 同源）。"""
    cfg = cfg or MacdDivCfg()
    df = data_portal.get_index_bars(index_1m_file, end_dt, count=int(lookback_bars))
    if df is None or df.empty or 'close' not in df.columns:
        return False
    close = df['close'].astype(float)
    if len(close) < max(int(cfg.MIN_BAND_LEN) + 2, 30):
        return False
    if 'datetime' in df.columns:
        close.index = pd.to_datetime(df['datetime'])
    macd_df = macd_from_close(close, cfg)
    div = DivergenceDetector(cfg).detect(close, macd_df)
    return bool(div.get('has_top'))


def _resolve_macd_cfg(cfg: Any) -> MacdDivCfg:
    if isinstance(cfg, MacdDivCfg):
        return cfg
    base = MacdDivCfg()
    return MacdDivCfg(**{f.name: getattr(cfg, f.name, getattr(base, f.name)) for f in fields(MacdDivCfg)})


class IncrementalMacdSession:
    # 模块说明
    # 输入参数：cfg、data_portal；可选 30m/1m 文件与 lookback_bars
    # 输出参数：IncrementalMacdSession 实例
    # 用途：盘中/无特征表时滚动缓存 30m close，每运行点仅 append 新 bar 后算 MACD
    """30m MACD 增量会话：滚动缓存 close 序列，仅 append 新 bar，供盘中/无特征表 fallback。"""

    def __init__(
        self,
        cfg: MacdDivCfg,
        data_portal: DataPortal,
        *,
        file_30m: str = '上证指数30分钟加入实时数据.csv',
        file_1m: str = '上证指数1分钟加入实时数据.csv',
        lookback_bars: int = 260,
    ):
        self.cfg = cfg
        self.data_portal = data_portal
        self.file_30m = file_30m
        self.file_1m = file_1m
        self.lookback_bars = int(lookback_bars)
        self._detector = DivergenceDetector(cfg)
        self._close: pd.Series = pd.Series(dtype=float)
        self._day_cache: dict[str, dict[str, Any]] = {}
        self._trade_date: Optional[date] = None

    # begin_day 方法说明
    # 输入参数：trade_date
    # 输出参数：无
    # 用途：新交易日清空当日结果缓存（close 滚动序列保留）
    def begin_day(self, trade_date: date | str) -> None:
        self._trade_date = pd.to_datetime(trade_date).date()
        self._day_cache.clear()

    @staticmethod
    def _bars_to_series(df: pd.DataFrame) -> pd.Series:
        if df is None or df.empty or 'close' not in df.columns:
            return pd.Series(dtype=float)
        close = df['close'].astype(float)
        if 'datetime' in df.columns:
            close.index = pd.to_datetime(df['datetime'])
        return close.sort_index()

    def _close_asof(self, end_ts: pd.Timestamp) -> pd.Series:
        end_ts = pd.Timestamp(end_ts)
        if self._close.empty:
            df = self.data_portal.get_index_bars(self.file_30m, end_ts, count=self.lookback_bars)
            self._close = self._bars_to_series(df)
        else:
            last_t = self._close.index[-1]
            if end_ts > last_t:
                frame = self.data_portal.load_frame(self.file_30m)
                if not frame.empty and 'datetime' in frame.columns:
                    dts = pd.to_datetime(frame['datetime'])
                    mask = (dts > last_t) & (dts <= end_ts)
                    new_df = frame.loc[mask]
                    if not new_df.empty:
                        new_close = self._bars_to_series(new_df)
                        self._close = pd.concat([self._close, new_close])
                        self._close = self._close[~self._close.index.duplicated(keep='last')].sort_index()
                        if len(self._close) > self.lookback_bars:
                            self._close = self._close.iloc[-self.lookback_bars:]
        if self._close.empty:
            return self._close
        return self._close[self._close.index <= end_ts]

    # check 方法说明
    # 输入参数：as_of（运行点时间字符串）
    # 输出参数：dict | None（divergence、current_price、has_1m_top、macd_mode=incremental）
    # 用途：增量计算截至 as_of 的 30m 背离与 1m 顶背离
    def check(self, as_of: str) -> Optional[dict[str, Any]]:
        end_ts = pd.to_datetime(as_of)
        day = end_ts.date()
        if self._trade_date != day:
            self.begin_day(day)
        cache_key = end_ts.strftime('%Y-%m-%d %H:%M')
        if cache_key in self._day_cache:
            return self._day_cache[cache_key]

        close = self._close_asof(end_ts)
        if close.empty:
            return None

        macd_df = macd_from_close(close, self.cfg)
        div = self._detector.detect(close, macd_df)
        cur = float(close.iloc[-1])
        bar_time = close.index[-1]
        has_1m_top = detect_1m_top_divergence(
            self.data_portal,
            as_of,
            self.cfg,
            index_1m_file=self.file_1m,
            lookback_bars=self.lookback_bars,
        )
        result = {
            'divergence': div,
            'current_price': cur,
            'timestamp': as_of,
            'bar_time': bar_time,
            'close_index': list(close.index),
            'close_values': [float(x) for x in close.values],
            'has_1m_top': bool(has_1m_top),
            'macd_mode': 'incremental',
        }
        self._day_cache[cache_key] = result
        return result


class MACDDivergenceModule:
    # 模块说明
    # 输入参数：cfg、data_portal；可选 impl 注入自定义 check
    # 输出参数：MACDDivergenceModule 实例
    # 用途：策略层 MACD 背离入口，默认委托 IncrementalMacdSession
    def __init__(self, cfg: Optional[Any] = None, data_portal: Optional[DataPortal] = None, impl: Optional[Any] = None):
        self.cfg = cfg or MacdDivCfg()
        self.data_portal = data_portal
        self.impl = impl
        self._session: Optional[IncrementalMacdSession] = None

    def _session_or_create(self) -> Optional[IncrementalMacdSession]:
        if self.data_portal is None:
            return None
        if self._session is None:
            file_30m = '上证指数30分钟加入实时数据.csv'
            file_1m = getattr(self.cfg, 'INDEX_1M_FILE', '上证指数1分钟加入实时数据.csv')
            lookback = int(getattr(self.cfg, 'LOOKBACK_BARS', 260) or 260)
            self._session = IncrementalMacdSession(
                _resolve_macd_cfg(self.cfg),
                self.data_portal,
                file_30m=file_30m,
                file_1m=file_1m,
                lookback_bars=lookback,
            )
        return self._session

    # begin_day 方法说明
    # 输入参数：trade_date
    # 输出参数：无
    # 用途：9:00 重置增量 MACD 当日缓存
    def begin_day(self, trade_date: date | str) -> None:
        session = self._session_or_create()
        if session is not None:
            session.begin_day(trade_date)

    # check 方法说明
    # 输入参数：context（as_of 时间字符串）、dm（兼容参数）
    # 输出参数：dict | None（见 IncrementalMacdSession.check）
    # 用途：运行点 MACD 背离检测
    def check(self, context, dm):
        if self.impl is not None:
            return self.impl.check(context, dm)
        session = self._session_or_create()
        if session is None:
            return None
        return session.check(context)

    def _check_local(self, context):
        session = self._session_or_create()
        if session is None:
            return None
        return session.check(context)

# 输入输出约定
# 1) MACDDivergenceModule.check(context, dm)
#    输入:
#      - context: 当前运行点时间/上下文对象，用于 asof 取数
#      - dm: 兼容参数，当前保留给上层策略调用
#    输出:
#      - dict 或 None
#        - divergence: 背离检测结果字典
#        - current_price: 当前收盘价（用于计数和交易判断）
#        - timestamp: 当前上下文时间
#        - close_index: 用于 MACD 计算的时间索引
#        - close_values: 用于 MACD 计算的收盘价序列
# 2) divergence 字典字段
#    - has_top: 是否顶背离
#    - has_bottom: 是否底背离
#    - top_detail: 顶背离详情，含 curr_band_start_time / curr_band_end_time / curr_max_price / prev_max_price / curr_max_hist / prev_max_hist / curr_band_uid
#    - bottom_detail: 底背离详情，含 curr_band_start_time / curr_band_end_time / curr_min_price / prev_min_price / curr_min_hist / prev_min_hist / curr_band_uid
# 3) update_counts_on_divergence(div_info, st)
#    输入:
#      - div_info: check() 的返回结果
#      - st: 状态字典，承接背离计数与去重 UID
#    输出:
#      - 原地更新 st，不返回新对象
#    状态字段:
#      - top_divergence_count / bottom_divergence_count
#      - last_counted_top_band_uid / last_counted_bottom_band_uid
#      - last_top_band_high_close / last_bottom_band_low_close
#      - last_top_band_start_time / last_top_band_end_time / last_top_band_max_hist
#      - last_bottom_band_start_time / last_bottom_band_end_time / last_bottom_band_min_hist
