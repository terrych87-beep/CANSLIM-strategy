"""开平仓交易条件判定模块。

输入参数：
  - market_status、div_info、StateManager、持仓、structure_bundle、time_str
输出参数：
  - TradeDecision（should_trade、action、side、position_type、reason 等）
用途：
  - 震荡/突破/背离等开仓与止损止盈平仓规则（v1/v2 分模型）

使用说明：
  - CenterMacdStrategy.handle_run_point 调用 check_close / check_open
  - 1m 顶背离优先读 div_info.has_1m_top（特征表预计算）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import pandas as pd

from strategy.macd_divergence import (
    MacdDivCfg,
    detect_1m_top_divergence,
    osc_rules_model_long,
    osc_rules_model_short,
)


# TradeDecision 说明
# 输入参数：各字段（should_trade、action、side、position_type、reason 等）
# 输出参数：TradeDecision 实例
# 用途：封装单次开平仓判定结果
@dataclass
class TradeDecision:
    should_trade: bool = False
    action: str = ''
    side: Optional[str] = None
    position_type: Optional[str] = None
    reason: str = ''
    contract_mode: str = 'next_contract'
    extra: Optional[dict] = None


# lock_should_apply 方法说明
# 输入参数：exit_reason（平仓原因字符串）
# 输出参数：bool，是否触发当日同向禁开
# 用途：止损/止盈平仓后设置 day_lock
def lock_should_apply(exit_reason) -> bool:
    if not exit_reason:
        return False
    text = str(exit_reason)
    if 'stop_loss' in text or 'take_profit' in text:
        return True
    return any(k in text for k in ('止损', '止盈'))


class TradingConditions:
    # 模块说明
    # 输入参数：cfg（策略配置）、data_portal（可选，1m 背离检测）
    # 输出参数：TradingConditions 实例
    # 用途：根据行情/MACD/结构状态判定开平仓
    def __init__(self, cfg, data_portal=None):
        self.cfg = cfg
        self.data_portal = data_portal
        self._macd_cfg = cfg if isinstance(cfg, MacdDivCfg) else MacdDivCfg()

    @staticmethod
    def _dt_to_str(x) -> str:
        if x is None:
            return ''
        if hasattr(x, 'strftime'):
            return x.strftime('%Y-%m-%d %H:%M:%S')
        try:
            return pd.to_datetime(x).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return str(x)

    @staticmethod
    def _as_timestamp_list(close_index):
        if not close_index:
            return []
        if isinstance(close_index[0], pd.Timestamp):
            return list(close_index)
        return [pd.to_datetime(t) for t in close_index]

    @staticmethod
    def _calc_bars_elapsed(close_index, start_bar_time):
        if not close_index or start_bar_time is None:
            return None
        try:
            st = pd.to_datetime(start_bar_time)
            cur_pos = len(close_index) - 1
            idx_ts = TradingConditions._as_timestamp_list(close_index)
            try:
                start_pos = idx_ts.index(st)
            except Exception:
                st_s = st.strftime('%Y-%m-%d %H:%M:%S')
                idx_s = [t.strftime('%Y-%m-%d %H:%M:%S') for t in idx_ts]
                if st_s not in idx_s:
                    return None
                start_pos = idx_s.index(st_s)
            return int(cur_pos - start_pos)
        except Exception:
            return None

    @staticmethod
    def _window_stats(close_index, close_values, start_bar_time):
        if not close_index or not close_values or start_bar_time is None:
            return None
        try:
            st = pd.to_datetime(start_bar_time)
            idx_ts = TradingConditions._as_timestamp_list(close_index)
            prices = [float(x) for x in close_values]
            if len(idx_ts) != len(prices):
                return None
            try:
                start_pos = idx_ts.index(st)
            except Exception:
                st_s = st.strftime('%Y-%m-%d %H:%M:%S')
                idx_s = [t.strftime('%Y-%m-%d %H:%M:%S') for t in idx_ts]
                if st_s not in idx_s:
                    return None
                start_pos = idx_s.index(st_s)
            win = prices[start_pos:]
            if not win:
                return None
            return {
                'bars_elapsed': int(len(prices) - 1 - start_pos),
                'min_close': float(min(win)),
                'max_close': float(max(win)),
                'latest_close': float(prices[-1]),
            }
        except Exception:
            return None

    @staticmethod
    def _in_range(px: float, rh, rl) -> bool:
        return (px > float(rl) * 0.995) and (px < float(rh) * 1.005)

    def _has_position(self, sm) -> bool:
        return bool(sm and sm.state.get('position_amount', 0) and int(sm.state.get('position_amount', 0)) > 0)

    def _blocked_by_day_lock(self, sm, side: str) -> bool:
        dl = sm.state.get('day_lock') or {}
        return bool(dl.get('ban_long')) if side == 'long' else bool(dl.get('ban_short'))

    def _cooldown_active(self, sm, current_date=None) -> bool:
        st = sm.state or {}
        s0 = st.get('long_range_break_cooldown_start_date')
        s1 = st.get('long_range_break_cooldown_end_date')
        if not s0 or not s1:
            return False
        try:
            if current_date is not None:
                today = pd.to_datetime(current_date).date()
            else:
                return False
            d0 = pd.to_datetime(s0).date()
            d1 = pd.to_datetime(s1).date()
            return (today > d0) and (today <= d1)
        except Exception:
            return False

    def calc_cooldown_end_date(self, start_date, days: int = 3) -> str:
        days = int(days or 3)
        start = pd.to_datetime(start_date).date()
        if self.data_portal is None:
            return (start + timedelta(days=days)).isoformat()
        trade_dates = self.data_portal.get_trade_dates()
        tds = sorted({pd.Timestamp(x).date() for x in trade_dates})
        if start not in tds:
            tds = sorted(set(tds) | {start})
        try:
            idx = tds.index(start)
            target_idx = idx + days
            if target_idx < len(tds):
                return tds[target_idx].isoformat()
        except Exception:
            pass
        return (start + timedelta(days=days)).isoformat()

    def _current_price(self, market_status) -> Optional[float]:
        if market_status is None:
            return None
        current_price = market_status.get('current_price') if isinstance(market_status, dict) else getattr(market_status, 'current_price', None)
        return None if current_price is None else float(current_price)

    def _trade_price(self, market_status, fallback: Optional[float] = None) -> Optional[float]:
        if market_status is None:
            return fallback
        trade_price = market_status.get('trade_price') if isinstance(market_status, dict) else getattr(market_status, 'trade_price', None)
        if trade_price is not None:
            return float(trade_price)
        return fallback

    def _div_flags(self, div_info):
        div = (div_info or {}).get('divergence') if isinstance(div_info, dict) else None
        has_top = bool(div.get('has_top')) if isinstance(div, dict) else False
        has_bottom = bool(div.get('has_bottom')) if isinstance(div, dict) else False
        return div, has_top, has_bottom

    def _osc_near_break_thresholds(self) -> tuple[float, float, float, float]:
        """震荡多/空各自的贴近、突破阈值（小数）。"""
        legacy_near = float(getattr(self.cfg, 'OSC_NEAR_REF_THRESHOLD', 0.005) or 0.005)
        legacy_break = float(getattr(self.cfg, 'OSC_BREAK_REF_THRESHOLD', 0.01) or 0.01)
        near_long = float(getattr(self.cfg, 'OSC_NEAR_REF_THRESHOLD_LONG', legacy_near) or legacy_near)
        break_long = float(getattr(self.cfg, 'OSC_BREAK_REF_THRESHOLD_LONG', legacy_break) or legacy_break)
        near_short = float(getattr(self.cfg, 'OSC_NEAR_REF_THRESHOLD_SHORT', legacy_near) or legacy_near)
        break_short = float(getattr(self.cfg, 'OSC_BREAK_REF_THRESHOLD_SHORT', legacy_break) or legacy_break)
        return near_long, break_long, near_short, break_short

    def _range_values(self, struct_snap):
        if not struct_snap:
            return None, None, False, None, None
        rh = struct_snap.get('range_high')
        rl = struct_snap.get('range_low')
        rs = bool(struct_snap.get('range_state'))
        rp = struct_snap.get('range_pct')
        center = struct_snap.get('center')
        return rh, rl, rs, rp, center

    def _wait_state(self, sm, group: str, key: str) -> dict:
        group_state = sm.state.get(group) or {}
        return group_state.get(key) or {}

    def _reset_wait(self, sm, group: str, key: str, payload: Optional[dict] = None) -> None:
        group_state = sm.state.get(group) or {}
        group_state[key] = payload or {'active': False}
        sm.state[group] = group_state

    def _detect_1m_top_divergence(self, end_dt) -> bool:
        if self.data_portal is None:
            return False
        return detect_1m_top_divergence(
            self.data_portal,
            end_dt,
            self._macd_cfg,
            index_1m_file=getattr(self.cfg, 'INDEX_1M_FILE', '上证指数1分钟加入实时数据.csv'),
            lookback_bars=int(getattr(self.cfg, 'LOOKBACK_BARS', 260) or 260),
        )

    def _close_pnl_metrics(self, sm, pos, judge_price: float) -> Optional[dict]:
        side = str(getattr(pos, 'side', '') or '')
        lots = int(getattr(pos, 'amount', 0) or 0)
        entry_price = getattr(pos, 'entry_price', None)
        anchor = sm.state.get('logical_anchor_entry') if sm else None
        if anchor is None:
            anchor = entry_price
        if anchor is None or lots <= 0:
            return None
        try:
            anchor_f = float(anchor)
            price_f = float(judge_price)
        except (TypeError, ValueError):
            return None
        if anchor_f <= 0:
            return None
        mult = 300.0
        if side == 'long':
            segment_pnl = (price_f - anchor_f) * lots * mult
        elif side == 'short':
            segment_pnl = (anchor_f - price_f) * lots * mult
        else:
            return None
        carry = float(sm.state.get('roll_carry_points', 0.0) or 0.0) if sm else 0.0
        total_pnl_amount = carry + segment_pnl
        denom = anchor_f * lots * mult
        pnl_ratio = total_pnl_amount / denom if denom > 0 else 0.0
        return {
            'pnl_ratio': pnl_ratio,
            'total_pnl_amount': total_pnl_amount,
            'segment_pnl': segment_pnl,
            'roll_carry_points': carry,
            'logical_anchor_entry': anchor_f,
            'judge_price': price_f,
        }

    # check_close 方法说明
    # 输入参数：market_status、div_info、sm（StateManager）、pos（持仓）、struct_snap、time_str
    # 输出参数：TradeDecision
    # 用途：运行点平仓判定（止损、止盈、背离平仓等）
    def check_close(self, market_status, div_info, sm, pos, struct_snap=None, time_str=None):
        if pos is None or int(getattr(pos, 'amount', 0) or 0) <= 0:
            return TradeDecision(False, reason='no_position')

        pos_type = str(getattr(pos, 'position_type', '') or '')
        side = str(getattr(pos, 'side', '') or '')
        trade_price = self._trade_price(market_status, fallback=None)
        entry_price = getattr(pos, 'entry_price', None)

        if trade_price is None:
            return TradeDecision(False, reason='missing_contract_price')
        if entry_price is None:
            return TradeDecision(False, reason='missing_price')

        judge_price = float(trade_price)
        pnl_metrics = self._close_pnl_metrics(sm, pos, judge_price)
        if pnl_metrics is None:
            return TradeDecision(False, reason='price_error')
        pnl_ratio = pnl_metrics['pnl_ratio']
        pnl_extra = {
            'pnl_ratio': pnl_ratio,
            'total_pnl_amount': pnl_metrics['total_pnl_amount'],
            'segment_pnl': pnl_metrics['segment_pnl'],
            'roll_carry_points': pnl_metrics['roll_carry_points'],
            'logical_anchor_entry': pnl_metrics['logical_anchor_entry'],
            'judge_price': pnl_metrics['judge_price'],
            'contract_price': judge_price,
        }

        div, has_top, has_bottom = self._div_flags(div_info)
        _, _, range_state, range_pct, _center = self._range_values(struct_snap)
        day_lock = sm.state.get('day_lock') or {}
        top_count = int(sm.state.get('top_divergence_count', 0) or 0)
        bottom_count = int(sm.state.get('bottom_divergence_count', 0) or 0)

        entry_top_cnt = sm.state.get('entry_top_cnt')
        try:
            entry_top_cnt = int(entry_top_cnt) if entry_top_cnt is not None else None
        except (TypeError, ValueError):
            entry_top_cnt = None
        delta_top_cnt = None
        if entry_top_cnt is not None:
            delta_top_cnt = max(0, top_count - entry_top_cnt)

        cur_time_str = time_str
        if cur_time_str is None and div_info:
            try:
                ts = div_info.get('timestamp')
                if ts is not None:
                    cur_time_str = pd.to_datetime(ts).strftime('%H:%M')
            except Exception:
                cur_time_str = None

        tp_profit = float(getattr(self.cfg, 'LONG_RANGE_BREAK_TP_PROFIT', 0.05) or 0.05)

        if pos_type == 'PT_LONG_RANGE_BREAK':
            if pnl_ratio <= -0.01 and not day_lock.get('ban_long'):
                return TradeDecision(True, 'close', side='long', position_type=pos_type, reason='long_break_stop_loss', extra={**pnl_extra, 'top_count': top_count})
            if delta_top_cnt is not None and delta_top_cnt >= 2:
                return TradeDecision(
                    True, 'close', side='long', position_type=pos_type,
                    reason='long_break_take_profit_by_top_divergence',
                    extra={**pnl_extra, 'top_count': top_count, 'entry_top_cnt': entry_top_cnt, 'delta_top_cnt': delta_top_cnt},
                )
            if pnl_ratio >= tp_profit:
                return TradeDecision(True, 'close', side='long', position_type=pos_type, reason='long_break_take_profit_5pct', extra={**pnl_extra, 'top_count': top_count})
            if (
                delta_top_cnt is not None
                and delta_top_cnt >= 1
                and pnl_ratio >= 0.01
                and cur_time_str in ('10:00', '10:30')
            ):
                end_dt = (div_info or {}).get('timestamp')
                if div_info is not None and 'has_1m_top' in div_info:
                    has_1m_top = bool(div_info.get('has_1m_top'))
                else:
                    has_1m_top = bool(end_dt and self._detect_1m_top_divergence(end_dt))
                if end_dt and has_1m_top:
                    return TradeDecision(
                        True, 'close', side='long', position_type=pos_type,
                        reason='long_break_take_profit_1m_top_divergence',
                        extra={**pnl_extra, 'top_count': top_count, 'entry_top_cnt': entry_top_cnt, 'delta_top_cnt': delta_top_cnt, 'time': cur_time_str},
                    )

        if pos_type == 'PT_SHORT_RANGE_BREAK':
            if pnl_ratio <= -0.01 and not day_lock.get('ban_short'):
                return TradeDecision(True, 'close', side='short', position_type=pos_type, reason='short_break_stop_loss', extra={**pnl_extra, 'bottom_count': bottom_count})
            if has_bottom and pnl_ratio >= 0.02:
                return TradeDecision(True, 'close', side='short', position_type=pos_type, reason='short_break_take_profit_bottom_divergence', extra={**pnl_extra, 'bottom_count': bottom_count})
            if pnl_ratio >= 0.05:
                return TradeDecision(True, 'close', side='short', position_type=pos_type, reason='short_break_take_profit_5pct', extra={**pnl_extra, 'bottom_count': bottom_count})

        if pos_type == 'PT_LONG_OSC':
            if pnl_ratio <= -0.005 and not has_bottom:
                return TradeDecision(True, 'close', side='long', position_type=pos_type, reason='long_osc_stop_loss', extra={**pnl_extra, 'bottom_count': bottom_count})
            if has_top:
                return TradeDecision(True, 'close', side='long', position_type=pos_type, reason='long_osc_take_profit_by_top_divergence', extra={**pnl_extra, 'top_count': top_count})
            if range_pct is not None and pnl_ratio >= float(range_pct) * 0.8:
                return TradeDecision(True, 'close', side='long', position_type=pos_type, reason='long_osc_take_profit_by_range_pct', extra=pnl_extra)

        if pos_type == 'PT_SHORT_OSC':
            if pnl_ratio <= -0.005 and not has_top:
                return TradeDecision(True, 'close', side='short', position_type=pos_type, reason='short_osc_stop_loss', extra={**pnl_extra, 'top_count': top_count})
            if has_bottom:
                return TradeDecision(True, 'close', side='short', position_type=pos_type, reason='short_osc_take_profit_by_bottom_divergence', extra={**pnl_extra, 'bottom_count': bottom_count})
            if range_pct is not None and pnl_ratio >= float(range_pct) * 0.8:
                return TradeDecision(True, 'close', side='short', position_type=pos_type, reason='short_osc_take_profit_by_range_pct', extra=pnl_extra)

        return TradeDecision(False, reason='hold_position', extra={**pnl_extra, 'range_state': range_state})

    # check_open 方法说明
    # 输入参数：context、market_status、div_info、sm、time_str、struct_snap、current_date
    # 输出参数：TradeDecision
    # 用途：运行点开仓判定（震荡、突破、背离开仓等）
    def check_open(self, context, market_status, div_info, sm, time_str, struct_snap=None, current_date=None):
        if self._has_position(sm):
            return TradeDecision(False, reason='has_position', extra={'stage': 'has_position'})

        if not div_info or not div_info.get('divergence'):
            return TradeDecision(False, reason='missing_divergence', extra={'stage': 'missing_divergence'})

        div, has_top, has_bottom = self._div_flags(div_info)
        if has_top and has_bottom:
            return TradeDecision(False, reason='conflict_divergence', extra={'stage': 'conflict_divergence'})

        current_price = div_info.get('current_price')
        if current_price is None:
            current_price = self._current_price(market_status)
        if current_price is None:
            return TradeDecision(False, reason='missing_price', extra={'stage': 'current_price'})

        close_index = div_info.get('close_index')
        close_values = div_info.get('close_values')
        range_high, range_low, range_state, range_pct, center = self._range_values(struct_snap)
        day_lock = sm.state.get('day_lock') or {}
        cooldown_active = self._cooldown_active(sm, current_date)
        top_count = int(sm.state.get('top_divergence_count', 0) or 0)
        bottom_count = int(sm.state.get('bottom_divergence_count', 0) or 0)

        breakthrough_window = int(getattr(self.cfg, 'BREAKTHROUGH_WINDOW_BARS', 18) or 18)
        near_long, break_long, near_short, break_short = self._osc_near_break_thresholds()
        short_pullback_window = int(getattr(self.cfg, 'SHORT_RANGE_BREAK_PULLBACK_WINDOW_BARS', 10) or 10)
        short_pullback_ratio = float(getattr(self.cfg, 'SHORT_RANGE_BREAK_PULLBACK_RATIO', 0.998) or 0.998)
        long_break_times = getattr(self.cfg, 'LONG_RANGE_BREAK_ALLOW_TIMES', ['14:57']) or ['14:57']

        debug_extra = {
            'stage': 'precheck',
            'current_price': current_price,
            'range_high': range_high,
            'range_low': range_low,
            'range_state': range_state,
            'range_pct': range_pct,
            'center_ready': bool(center),
            'has_top': has_top,
            'has_bottom': has_bottom,
            'top_count': top_count,
            'bottom_count': bottom_count,
            'day_lock': day_lock,
            'cooldown_active': cooldown_active,
            'breakthrough_window_bars': breakthrough_window,
        }

        if range_high is None or range_low is None:
            return TradeDecision(False, reason='range_not_ready', extra={**debug_extra, 'branch': 'range_not_ready'})

        px = float(current_price)
        rh = float(range_high)
        rl = float(range_low)
        break_up = px >= rh * 1.01
        break_down = px <= rl * 0.99
        in_range = self._in_range(px, rh, rl)

        if time_str in long_break_times:
            if break_up and not sm.state.get('breakout_up_triggered') and not cooldown_active and not self._blocked_by_day_lock(sm, 'long'):
                return TradeDecision(True, 'open', side='long', position_type='PT_LONG_RANGE_BREAK', reason='14:57_long_range_break', extra={**debug_extra, 'branch': '1457_long_break', 'wait_bars': 0})
            return TradeDecision(False, reason='14:57_only_long_break_allowed', extra={**debug_extra, 'branch': '1457_gate'})

        range_break_wait = self._wait_state(sm, 'range_break_wait', 'sell')
        if break_down:
            self._reset_wait(sm, 'range_break_wait', 'sell', {
                'active': True,
                'ref_rl': rl,
                'start_bar_time': div_info.get('bar_time'),
            })
            range_break_wait = self._wait_state(sm, 'range_break_wait', 'sell')

        if range_break_wait.get('active') and range_break_wait.get('ref_rl') is not None and range_break_wait.get('start_bar_time') is not None:
            bars_elapsed = self._calc_bars_elapsed(close_index, range_break_wait.get('start_bar_time'))
            if bars_elapsed is None or bars_elapsed > short_pullback_window:
                self._reset_wait(sm, 'range_break_wait', 'sell', {'active': False, 'ref_rl': None, 'start_bar_time': None})
            else:
                ref_rl = float(range_break_wait.get('ref_rl'))
                if px >= ref_rl * short_pullback_ratio and not sm.state.get('breakout_down_triggered') and not self._blocked_by_day_lock(sm, 'short'):
                    self._reset_wait(sm, 'range_break_wait', 'sell', {'active': False, 'ref_rl': None, 'start_bar_time': None})
                    return TradeDecision(
                        True, 'open', side='short', position_type='PT_SHORT_RANGE_BREAK',
                        reason='range_break_sell_confirmed',
                        extra={**debug_extra, 'branch': 'range_break_confirm', 'ref_rl': ref_rl, 'bars_elapsed': bars_elapsed},
                    )

        if in_range:
            ow = sm.state.get('osc_wait') or {}
            rules_model_long = osc_rules_model_long(self.cfg)
            rules_model_short = osc_rules_model_short(self.cfg)
            w_long = ow.get('long') or {}
            if rules_model_long == 'v1' and w_long.get('active') and w_long.get('start_bar_time') and w_long.get('ref_close') is not None:
                win = self._window_stats(close_index, close_values, w_long.get('start_bar_time'))
                if win is None or win.get('bars_elapsed') is None or win.get('bars_elapsed') > breakthrough_window:
                    ow['long'] = {'active': False, 'start_bar_time': None, 'ref_close': None, 'ref_band_uid': None}
                    sm.state['osc_wait'] = ow
                else:
                    ref_close = float(w_long.get('ref_close'))
                    min_wait = float(win.get('min_close'))
                    near_low = (px / min_wait - 1.0) if min_wait > 0 else None
                    dd_from_ref = (min_wait / ref_close - 1.0) if ref_close > 0 else None
                    if (
                        near_low is not None
                        and dd_from_ref is not None
                        and near_low < near_long
                        and dd_from_ref >= -break_long
                    ):
                        if not self._blocked_by_day_lock(sm, 'long') and not cooldown_active:
                            return TradeDecision(
                                True, 'open', side='long', position_type='PT_LONG_OSC',
                                reason='osc_long_by_bottom_divergence',
                                extra={
                                    **debug_extra,
                                    'branch': 'osc_long',
                                    'osc_rules_model': rules_model_long,
                                    'bars_elapsed': win.get('bars_elapsed'),
                                    'ref_close': ref_close,
                                    'near_low_ratio': near_low,
                                    'drawdown_from_div_close': dd_from_ref,
                                },
                            )

            w_short = ow.get('short') or {}
            if rules_model_short == 'v1' and w_short.get('active') and w_short.get('start_bar_time') and w_short.get('ref_close') is not None:
                win = self._window_stats(close_index, close_values, w_short.get('start_bar_time'))
                if win is None or win.get('bars_elapsed') is None or win.get('bars_elapsed') > breakthrough_window:
                    ow['short'] = {'active': False, 'start_bar_time': None, 'ref_close': None, 'ref_band_uid': None}
                    sm.state['osc_wait'] = ow
                else:
                    ref_close = float(w_short.get('ref_close'))
                    max_wait = float(win.get('max_close'))
                    near_high = (max_wait / px - 1.0) if px > 0 else None
                    rise_from_ref = (max_wait / ref_close - 1.0) if ref_close > 0 else None
                    if (
                        near_high is not None
                        and rise_from_ref is not None
                        and near_high < near_short
                        and rise_from_ref <= break_short
                    ):
                        if not self._blocked_by_day_lock(sm, 'short'):
                            return TradeDecision(
                                True, 'open', side='short', position_type='PT_SHORT_OSC',
                                reason='osc_short_by_top_divergence',
                                extra={
                                    **debug_extra,
                                    'branch': 'osc_short',
                                    'osc_rules_model': rules_model_short,
                                    'bars_elapsed': win.get('bars_elapsed'),
                                    'ref_close': ref_close,
                                    'near_high_ratio': near_high,
                                    'rise_from_div_close': rise_from_ref,
                                },
                            )

            if rules_model_long != 'v1' and w_long.get('active') and w_long.get('start_bar_time') and w_long.get('ref_low') is not None:
                win = self._window_stats(close_index, close_values, w_long.get('start_bar_time'))
                if win is None or win.get('bars_elapsed') is None or win.get('bars_elapsed') > breakthrough_window:
                    ow['long'] = {'active': False, 'start_bar_time': None, 'ref_low': None, 'ref_band_uid': None}
                    sm.state['osc_wait'] = ow
                else:
                    ref_low = float(w_long.get('ref_low'))
                    min_wait = float(win.get('min_close'))
                    # 贴近参考点：当前价相对 ref_low 涨幅不超过阈值（还没怎么涨）
                    rise_from_ref_low = (px / ref_low - 1.0) if ref_low > 0 else None
                    break_from_ref = (min_wait / ref_low - 1.0) if ref_low > 0 else None
                    if (
                        rise_from_ref_low is not None
                        and break_from_ref is not None
                        and rise_from_ref_low <= near_long
                        and break_from_ref >= -break_long
                    ):
                        if not self._blocked_by_day_lock(sm, 'long') and not cooldown_active:
                            return TradeDecision(
                                True, 'open', side='long', position_type='PT_LONG_OSC',
                                reason='osc_long_by_bottom_divergence',
                                extra={
                                    **debug_extra,
                                    'branch': 'osc_long',
                                    'bars_elapsed': win.get('bars_elapsed'),
                                    'ref_low': ref_low,
                                    'min_wait_close': min_wait,
                                    'rise_from_ref_low': rise_from_ref_low,
                                    'break_from_ref_ratio': break_from_ref,
                                    'near_ref_threshold': near_long,
                                    'break_ref_threshold': break_long,
                                },
                            )

            if rules_model_short != 'v1' and w_short.get('active') and w_short.get('start_bar_time') and w_short.get('ref_high') is not None:
                win = self._window_stats(close_index, close_values, w_short.get('start_bar_time'))
                if win is None or win.get('bars_elapsed') is None or win.get('bars_elapsed') > breakthrough_window:
                    ow['short'] = {'active': False, 'start_bar_time': None, 'ref_high': None, 'ref_band_uid': None}
                    sm.state['osc_wait'] = ow
                else:
                    ref_high = float(w_short.get('ref_high'))
                    max_wait = float(win.get('max_close'))
                    # 贴近参考点：当前价相对 ref_high 跌幅不超过阈值（还没怎么跌）
                    drop_from_ref_high = (ref_high - px) / ref_high if ref_high > 0 else None
                    break_from_ref = (max_wait / ref_high - 1.0) if ref_high > 0 else None
                    if (
                        drop_from_ref_high is not None
                        and break_from_ref is not None
                        and drop_from_ref_high <= near_short
                        and break_from_ref <= break_short
                    ):
                        if not self._blocked_by_day_lock(sm, 'short'):
                            return TradeDecision(
                                True, 'open', side='short', position_type='PT_SHORT_OSC',
                                reason='osc_short_by_top_divergence',
                                extra={
                                    **debug_extra,
                                    'branch': 'osc_short',
                                    'bars_elapsed': win.get('bars_elapsed'),
                                    'ref_high': ref_high,
                                    'max_wait_close': max_wait,
                                    'drop_from_ref_high': drop_from_ref_high,
                                    'break_from_ref_ratio': break_from_ref,
                                    'near_ref_threshold': near_short,
                                    'break_ref_threshold': break_short,
                                },
                            )

        return TradeDecision(False, reason='no_open_signal', extra={**debug_extra, 'branch': 'no_signal'})
