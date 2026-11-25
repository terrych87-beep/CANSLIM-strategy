# 模块说明
# 输入参数：各 setter（side、contract、amount、等待态、day_lock 等）
# 输出参数：self.state（dict，持仓/背离计数/等待态/移仓标记）
# 用途：策略各模块读写统一状态；reset_daily() 在 9:00 调用
# 使用说明：由 CenterMacdStrategy 持有；各公共方法有独立方法说明

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class StateManager:
    # 状态管理说明
    # 输入参数：state_path（状态持久化路径，可选）
    # 输出参数：StateManager 实例
    # 中文映射：
    #   - state_path -> 状态持久化路径
    #   - state -> 统一状态字典
    #   - position_* -> 持仓相关字段
    #   - *_divergence_* -> 背离统计字段
    #   - *_wait -> 等待态字段
    #   - day_lock -> 日内锁字段
    #   - switch_* -> 移仓字段
    # 用途：统一承接策略运行时状态，避免散落在各模块中

    # __init__ 方法说明
    # 输入参数：state_path（可选，状态 JSON 持久化路径）
    # 输出参数：无（初始化 self.state = _init()）
    # 用途：构造状态管理器实例
    def __init__(self, state_path: str | Path | None = None):
        self.state_path = Path(state_path) if state_path is not None else None
        self.state: Dict[str, Any] = self._init()

    # _init 方法说明
    # 输入参数：无
    # 输出参数：dict，state 初始默认值（持仓/背离/等待/锁/结构/移仓六区）
    # 用途：构造 StateManager 时初始化统一状态字典
    def _init(self) -> Dict[str, Any]:
        return {
            # =========================
            # 1. 持仓状态区
            # =========================
            # position_side -> 持仓方向
            # position_type -> 持仓类型
            # entry_price -> 入场价
            # entry_time -> 入场时间
            # entry_top_cnt -> 入场时顶背离计数快照
            # position_contract -> 持仓合约
            # position_amount -> 持仓数量
            'position_side': None,
            'position_type': None,
            'entry_price': None,
            'entry_time': None,
            'entry_top_cnt': None,
            'position_contract': None,
            'position_amount': 0,

            # =========================
            # 2. 背离计数状态区
            # =========================
            # bottom_divergence_count -> 底背离计数
            # top_divergence_count -> 顶背离计数
            # last_counted_top_band_uid -> 最近一次统计的顶背离波段ID
            # last_counted_bottom_band_uid -> 最近一次统计的底背离波段ID
            # last_bottom_band_low_close -> 最近底背离波段的低收盘价
            # last_top_band_high_close -> 最近顶背离波段的高收盘价
            # last_bottom_band_start_time -> 最近底背离波段开始时间
            # last_bottom_band_end_time -> 最近底背离波段结束时间
            # last_bottom_band_min_hist -> 最近底背离波段最小柱值
            # last_top_band_start_time -> 最近顶背离波段开始时间
            # last_top_band_end_time -> 最近顶背离波段结束时间
            # last_top_band_max_hist -> 最近顶背离波段最大柱值
            'bottom_divergence_count': 0,
            'top_divergence_count': 0,
            'last_counted_top_band_uid': None,
            'last_counted_bottom_band_uid': None,
            'last_bottom_band_low_close': None,
            'last_top_band_high_close': None,
            'last_bottom_band_start_time': None,
            'last_bottom_band_end_time': None,
            'last_bottom_band_min_hist': None,
            'last_top_band_start_time': None,
            'last_top_band_end_time': None,
            'last_top_band_max_hist': None,

            # =========================
            # 3. 等待状态区
            # =========================
            # break_wait -> 突破等待态
            #   buy / sell -> 多/空等待分支
            #   active -> 是否激活
            #   ref_price -> 参考价格
            #   ref_bar_time -> 参考bar时间
            #   start_bar_time -> 起始bar时间
            #   start_bar_index -> 起始bar索引
            #   used -> 是否已使用
            #   count_at_div -> 背离计数快照
            #   valid_bars -> 有效bar数
            #   ref_band_uid -> 参考波段ID
            'break_wait': {
                'buy': {'active': False, 'ref_price': None, 'ref_bar_time': None, 'start_bar_time': None, 'start_bar_index': None, 'used': False, 'count_at_div': None, 'valid_bars': 0, 'ref_band_uid': None},
                'sell': {'active': False, 'ref_price': None, 'ref_bar_time': None, 'start_bar_time': None, 'start_bar_index': None, 'used': False, 'count_at_div': None, 'valid_bars': 0, 'ref_band_uid': None},
            },
            # osc_wait -> 震荡等待态
            #   long / short -> 多/空等待分支
            #   active -> 是否激活
            #   waiting -> 是否处于等待中
            #   start_bar_time -> 起始bar时间
            #   start_bar_index -> 起始bar索引
            #   ref_low / ref_high -> 参考低点/参考高点（背离波段极值收盘价）
            #   ref_band_uid -> 参考波段ID
            #   div_count_at_start -> 起始时背离计数
            #   valid_bars -> 有效bar数
            'osc_wait': {
                'long': {'active': False, 'waiting': False, 'start_bar_time': None, 'start_bar_index': None, 'ref_low': None, 'ref_band_uid': None, 'div_count_at_start': None, 'valid_bars': 0},
                'short': {'active': False, 'waiting': False, 'start_bar_time': None, 'start_bar_index': None, 'ref_high': None, 'ref_band_uid': None, 'div_count_at_start': None, 'valid_bars': 0},
            },
            # range_break_wait -> 区间突破等待态
            #   sell -> 空头突破等待分支
            #   active -> 是否激活
            #   ref_rl -> 参考下沿
            #   start_bar_time -> 起始bar时间
            #   start_bar_index -> 起始bar索引
            #   valid_bars -> 有效bar数
            #   wait_start_price -> 等待起始价格
            'range_break_wait': {'sell': {'active': False, 'ref_rl': None, 'start_bar_time': None, 'start_bar_index': None, 'valid_bars': 0, 'wait_start_price': None}},

            # =========================
            # 4. 日内锁与冷却区
            # =========================
            # day_lock -> 当日禁开锁
            # breakout_up_triggered -> 突破多是否已触发
            # breakout_down_triggered -> 突破空是否已触发
            # long_range_break_cooldown_start_date -> 多头区间突破冷却开始日
            # long_range_break_cooldown_end_date -> 多头区间突破冷却结束日
            'day_lock': {'date': None, 'ban_long': False, 'ban_short': False, 'ban_long_reason': None, 'ban_short_reason': None},
            'breakout_up_triggered': False,
            'breakout_down_triggered': False,
            'long_range_break_cooldown_start_date': None,
            'long_range_break_cooldown_end_date': None,

            # =========================
            # 5. 结构快照区
            # =========================
            # structure_bundle -> 日线结构快照
            'structure_bundle': None,

            # =========================
            # 6. 移仓状态区
            # =========================
            # switch_pending -> 是否有待处理移仓
            # switch_old -> 旧合约
            # switch_new -> 新合约
            # switch_side -> 移仓方向
            # switch_target_lots -> 目标手数
            # switch_stage -> 移仓阶段
            # switch_retry_count -> 移仓重试次数
            # logical_anchor_entry -> 逻辑锚定入场价
            # roll_carry_points -> 移仓累计点数
            'switch_pending': False,
            'switch_old': None,
            'switch_new': None,
            'switch_side': None,
            'switch_target_lots': 0,
            'switch_stage': None,
            'switch_retry_count': 0,
            'logical_anchor_entry': None,
            'roll_carry_points': 0.0,
        }

    # _ensure_group_defaults 方法说明
    # 输入参数：无
    # 输出参数：无（setdefault 补全 break_wait/osc_wait/range_break_wait/day_lock）
    # 用途：reset_daily 前确保嵌套等待态结构存在
    def _ensure_group_defaults(self) -> None:
        self.state.setdefault('break_wait', {
            'buy': {'active': False, 'ref_price': None, 'ref_bar_time': None, 'start_bar_time': None, 'start_bar_index': None, 'used': False, 'count_at_div': None, 'valid_bars': 0, 'ref_band_uid': None},
            'sell': {'active': False, 'ref_price': None, 'ref_bar_time': None, 'start_bar_time': None, 'start_bar_index': None, 'used': False, 'count_at_div': None, 'valid_bars': 0, 'ref_band_uid': None},
        })
        self.state.setdefault('osc_wait', {
            'long': {'active': False, 'waiting': False, 'start_bar_time': None, 'start_bar_index': None, 'ref_low': None, 'ref_band_uid': None, 'div_count_at_start': None, 'valid_bars': 0},
            'short': {'active': False, 'waiting': False, 'start_bar_time': None, 'start_bar_index': None, 'ref_high': None, 'ref_band_uid': None, 'div_count_at_start': None, 'valid_bars': 0},
        })
        self.state.setdefault('range_break_wait', {'sell': {'active': False, 'ref_rl': None, 'start_bar_time': None, 'start_bar_index': None, 'valid_bars': 0, 'wait_start_price': None}})
        self.state.setdefault('day_lock', {'date': None, 'ban_long': False, 'ban_short': False, 'ban_long_reason': None, 'ban_short_reason': None})

    # reset_divergence_state 方法说明
    # 输入参数：无
    # 输出参数：无（顶底计数与波段 UID/极值时间等归零）
    # 用途：手动或测试时清空背离统计（日常回测较少直接调用）
    def reset_divergence_state(self) -> None:
        self.state['bottom_divergence_count'] = 0
        self.state['top_divergence_count'] = 0
        self.state['last_counted_top_band_uid'] = None
        self.state['last_counted_bottom_band_uid'] = None
        self.state['last_bottom_band_low_close'] = None
        self.state['last_top_band_high_close'] = None
        self.state['last_bottom_band_start_time'] = None
        self.state['last_bottom_band_end_time'] = None
        self.state['last_bottom_band_min_hist'] = None
        self.state['last_top_band_start_time'] = None
        self.state['last_top_band_end_time'] = None
        self.state['last_top_band_max_hist'] = None

    # reset_intraday_flags 方法说明
    # 输入参数：无
    # 输出参数：无（breakout_up/down_triggered 置 False，重置 range_break_wait.sell）
    # 用途：新交易日清除日内突破触发标记
    def reset_intraday_flags(self) -> None:
        self.state['breakout_up_triggered'] = False
        self.state['breakout_down_triggered'] = False
        self.state['range_break_wait'] = {'sell': {'active': False, 'ref_rl': None, 'start_bar_time': None}}

    # reset_wait_state 方法说明
    # 输入参数：无
    # 输出参数：无（break_wait、osc_wait、range_break_wait 恢复默认空态）
    # 用途：清空全部等待态（突破/震荡/区间突破空）
    def reset_wait_state(self) -> None:
        self.state['break_wait'] = {
            'buy': {'active': False, 'ref_price': None, 'ref_bar_time': None, 'start_bar_time': None, 'start_bar_index': None, 'used': False, 'count_at_div': None, 'valid_bars': 0, 'ref_band_uid': None},
            'sell': {'active': False, 'ref_price': None, 'ref_bar_time': None, 'start_bar_time': None, 'start_bar_index': None, 'used': False, 'count_at_div': None, 'valid_bars': 0, 'ref_band_uid': None},
        }
        self.state['osc_wait'] = {
            'long': {'active': False, 'waiting': False, 'start_bar_time': None, 'start_bar_index': None, 'ref_low': None, 'ref_band_uid': None, 'div_count_at_start': None, 'valid_bars': 0},
            'short': {'active': False, 'waiting': False, 'start_bar_time': None, 'start_bar_index': None, 'ref_high': None, 'ref_band_uid': None, 'div_count_at_start': None, 'valid_bars': 0},
        }
        self.state['range_break_wait'] = {'sell': {'active': False, 'ref_rl': None, 'start_bar_time': None, 'start_bar_index': None, 'valid_bars': 0, 'wait_start_price': None}}

    # reset_daily 方法说明
    # 输入参数：current_date_str（交易日 YYYY-MM-DD）
    # 输出参数：无（更新 day_lock、清除日内 breakout/移仓标记）
    # 用途：9:00 新交易日初始化
    def reset_daily(self, current_date_str: str) -> None:
        self._ensure_group_defaults()
        dl = self.state.get('day_lock') or {}
        if dl.get('date') != current_date_str:
            self.state['day_lock'] = {'date': current_date_str, 'ban_long': False, 'ban_short': False, 'ban_long_reason': None, 'ban_short_reason': None}
            self.reset_intraday_flags()
            self.clear_rollover_flags()

    # clear_position 方法说明
    # 输入参数：无
    # 输出参数：无（清空 position_*、logical_anchor_entry、roll_carry_points）
    # 用途：无持仓时重置台账持仓区
    def clear_position(self) -> None:
        for k in ('position_side', 'position_contract', 'entry_price', 'position_type', 'entry_time', 'entry_top_cnt'):
            self.state[k] = None
        self.state['position_amount'] = 0
        self.state['logical_anchor_entry'] = None
        self.state['roll_carry_points'] = 0.0

    # clear_position_on_close 方法说明
    # 输入参数：无
    # 输出参数：无（clear_position + 清空 switch_* 移仓标记）
    # 用途：平仓后清理持仓与待移仓状态
    def clear_position_on_close(self) -> None:
        self.clear_position()
        self.state['switch_pending'] = False
        self.state['switch_old'] = None
        self.state['switch_new'] = None
        self.state['switch_side'] = None
        self.state['switch_target_lots'] = 0
        self.state['switch_stage'] = None

    # clear_position_on_rollover 方法说明
    # 输入参数：无
    # 输出参数：无（仅清 position_* 字段，保留 switch 流程标记）
    # 用途：移仓平旧仓后暂清持仓字段
    def clear_position_on_rollover(self) -> None:
        self.state['position_side'] = None
        self.state['position_contract'] = None
        self.state['entry_price'] = None
        self.state['position_type'] = None
        self.state['entry_time'] = None
        self.state['entry_top_cnt'] = None
        self.state['position_amount'] = 0

    # clear_rollover_flags 方法说明
    # 输入参数：无
    # 输出参数：无（switch_pending/old/new/side/stage/retry_count 复位）
    # 用途：移仓完成或新交易日清除移仓流程标记
    def clear_rollover_flags(self) -> None:
        self.state['switch_pending'] = False
        self.state['switch_old'] = None
        self.state['switch_new'] = None
        self.state['switch_side'] = None
        self.state['switch_target_lots'] = 0
        self.state['switch_stage'] = None
        self.state['switch_retry_count'] = 0

    # sync_from_portfolio 方法说明
    # 输入参数：portfolio（Portfolio 实例）
    # 输出参数：无（从 portfolio.position 反向写入 state 持仓区；空仓则 clear_position）
    # 用途：撮合后与 Portfolio 对账同步台账
    def sync_from_portfolio(self, portfolio) -> None:
        pos = getattr(portfolio, 'position', None)
        if pos is None or getattr(pos, 'amount', 0) <= 0:
            self.clear_position()
            return
        self.state['position_side'] = pos.side
        self.state['position_type'] = pos.position_type
        self.state['position_contract'] = pos.contract
        self.state['position_amount'] = pos.amount
        self.state['entry_price'] = pos.entry_price

    # set_position 方法说明
    # 输入参数：side、position_type、contract、amount、entry_price 等
    # 输出参数：无（写入 state 持仓区）
    # 用途：开仓/移仓后同步台账持仓
    def set_position(self, *, side: str, position_type: str | None, contract: str, amount: int, entry_price: float, entry_time: str | None = None, entry_top_cnt: int | None = None, logical_anchor_entry: float | None = None) -> None:
        self.state['position_side'] = side
        self.state['position_type'] = position_type
        self.state['position_contract'] = contract
        self.state['position_amount'] = int(amount)
        self.state['entry_price'] = float(entry_price) if entry_price is not None else None
        self.state['entry_time'] = entry_time
        self.state['entry_top_cnt'] = entry_top_cnt
        self.state['logical_anchor_entry'] = logical_anchor_entry if logical_anchor_entry is not None else entry_price

    # set_day_lock 方法说明
    # 输入参数：current_date、ban_long、ban_short、ban_*_reason
    # 输出参数：无（写入 day_lock 字典）
    # 用途：设置当日禁多/禁空及原因
    def set_day_lock(self, *, current_date: str, ban_long: bool = False, ban_short: bool = False, ban_long_reason: str | None = None, ban_short_reason: str | None = None) -> None:
        self.state['day_lock'] = {
            'date': current_date,
            'ban_long': bool(ban_long),
            'ban_short': bool(ban_short),
            'ban_long_reason': ban_long_reason,
            'ban_short_reason': ban_short_reason,
        }

    # ban_long_today 方法说明
    # 输入参数：current_date、reason
    # 输出参数：无（day_lock.ban_long=True，保留既有 ban_short）
    # 用途：止损/止盈平多后当日禁止再开多
    def ban_long_today(self, current_date: str, reason: str | None = None) -> None:
        dl = self.state.get('day_lock') or {}
        self.state['day_lock'] = {
            'date': current_date,
            'ban_long': True,
            'ban_short': bool(dl.get('ban_short', False)),
            'ban_long_reason': reason,
            'ban_short_reason': dl.get('ban_short_reason'),
        }

    # ban_short_today 方法说明
    # 输入参数：current_date、reason
    # 输出参数：无（day_lock.ban_short=True，保留既有 ban_long）
    # 用途：止损/止盈平空后当日禁止再开空
    def ban_short_today(self, current_date: str, reason: str | None = None) -> None:
        dl = self.state.get('day_lock') or {}
        self.state['day_lock'] = {
            'date': current_date,
            'ban_long': bool(dl.get('ban_long', False)),
            'ban_short': True,
            'ban_long_reason': dl.get('ban_long_reason'),
            'ban_short_reason': reason,
        }

    # set_break_wait 方法说明
    # 输入参数：side（buy/sell）、ref_price、ref_bar_time、start_bar_*、count_at_div、ref_band_uid
    # 输出参数：无（激活 break_wait[side] 并写入参考价/时间）
    # 用途：背离后启动突破等待窗口
    def set_break_wait(self, side: str, *, ref_price: float | None = None, ref_bar_time: str | None = None, start_bar_time: str | None = None, start_bar_index: int | None = None, count_at_div: int | None = None, ref_band_uid: str | None = None) -> None:
        if side not in ('buy', 'sell'):
            return
        self.state.setdefault('break_wait', {})[side] = {
            'active': True,
            'ref_price': ref_price,
            'ref_bar_time': ref_bar_time,
            'start_bar_time': start_bar_time,
            'start_bar_index': start_bar_index,
            'used': False,
            'count_at_div': count_at_div,
            'valid_bars': 0,
            'ref_band_uid': ref_band_uid,
        }

    # increment_break_wait_bars 方法说明
    # 输入参数：side（buy/sell）
    # 输出参数：无（active 时 valid_bars += 1）
    # 用途：每个运行点累加突破等待有效 bar 数
    def increment_break_wait_bars(self, side: str) -> None:
        item = (self.state.get('break_wait') or {}).get(side) or {}
        if item.get('active'):
            item['valid_bars'] = int(item.get('valid_bars', 0) or 0) + 1
            self.state.setdefault('break_wait', {})[side] = item

    # clear_break_wait 方法说明
    # 输入参数：side（buy/sell）
    # 输出参数：无（break_wait[side] 恢复默认非激活态）
    # 用途：突破成交或超时后清除等待
    def clear_break_wait(self, side: str) -> None:
        if side not in ('buy', 'sell'):
            return
        self.state.setdefault('break_wait', {})[side] = {'active': False, 'ref_price': None, 'ref_bar_time': None, 'start_bar_time': None, 'start_bar_index': None, 'used': False, 'count_at_div': None, 'valid_bars': 0, 'ref_band_uid': None}

    # set_osc_wait 方法说明
    # 输入参数：side（long/short）、ref_low/ref_high、ref_band_uid、start_bar_*、div_count_at_start
    # 输出参数：无（激活 osc_wait[side] 震荡等待态）
    # 用途：背离后启动震荡开仓等待（v1 ref_close / v2 ref_low-high）
    def set_osc_wait(
        self,
        side: str,
        *,
        ref_low: float | None = None,
        ref_high: float | None = None,
        ref_band_uid: str | None = None,
        start_bar_time: str | None = None,
        start_bar_index: int | None = None,
        div_count_at_start: int | None = None,
    ) -> None:
        if side not in ('long', 'short'):
            return
        payload = {
            'active': True,
            'waiting': True,
            'start_bar_time': start_bar_time,
            'start_bar_index': start_bar_index,
            'ref_band_uid': ref_band_uid,
            'div_count_at_start': div_count_at_start,
            'valid_bars': 0,
        }
        if side == 'long':
            payload['ref_low'] = ref_low
        else:
            payload['ref_high'] = ref_high
        self.state.setdefault('osc_wait', {})[side] = payload

    # increment_osc_wait_bars 方法说明
    # 输入参数：side（long/short）
    # 输出参数：无（active 时 valid_bars += 1）
    # 用途：每个运行点累加震荡等待有效 bar 数
    def increment_osc_wait_bars(self, side: str) -> None:
        item = (self.state.get('osc_wait') or {}).get(side) or {}
        if item.get('active'):
            item['valid_bars'] = int(item.get('valid_bars', 0) or 0) + 1
            self.state.setdefault('osc_wait', {})[side] = item

    # clear_osc_wait 方法说明
    # 输入参数：side（long/short）
    # 输出参数：无（osc_wait[side] 恢复默认非激活态）
    # 用途：震荡开仓成交或条件失效后清除等待
    def clear_osc_wait(self, side: str) -> None:
        if side not in ('long', 'short'):
            return
        empty = {'active': False, 'waiting': False, 'start_bar_time': None, 'start_bar_index': None, 'ref_band_uid': None, 'div_count_at_start': None, 'valid_bars': 0}
        if side == 'long':
            empty['ref_low'] = None
        else:
            empty['ref_high'] = None
        self.state.setdefault('osc_wait', {})[side] = empty

    # set_range_break_wait_sell 方法说明
    # 输入参数：ref_rl（区间下沿）、start_bar_time、start_bar_index、wait_start_price
    # 输出参数：无（激活 range_break_wait.sell）
    # 用途：价格跌破 range 下沿后启动空头突破确认等待
    def set_range_break_wait_sell(self, *, ref_rl: float | None = None, start_bar_time: str | None = None, start_bar_index: int | None = None, wait_start_price: float | None = None) -> None:
        self.state.setdefault('range_break_wait', {})['sell'] = {
            'active': True,
            'ref_rl': ref_rl,
            'start_bar_time': start_bar_time,
            'start_bar_index': start_bar_index,
            'valid_bars': 0,
            'wait_start_price': wait_start_price,
        }

    # increment_range_break_wait_sell_bars 方法说明
    # 输入参数：无
    # 输出参数：无（sell 分支 active 时 valid_bars += 1）
    # 用途：累加区间突破空等待有效 bar 数
    def increment_range_break_wait_sell_bars(self) -> None:
        item = (self.state.get('range_break_wait') or {}).get('sell') or {}
        if item.get('active'):
            item['valid_bars'] = int(item.get('valid_bars', 0) or 0) + 1
            self.state.setdefault('range_break_wait', {})['sell'] = item

    # clear_range_break_wait_sell 方法说明
    # 输入参数：无
    # 输出参数：无（range_break_wait.sell 恢复默认非激活态）
    # 用途：突破空成交或价格回到区间内后清除等待
    def clear_range_break_wait_sell(self) -> None:
        self.state.setdefault('range_break_wait', {})['sell'] = {'active': False, 'ref_rl': None, 'start_bar_time': None, 'start_bar_index': None, 'valid_bars': 0, 'wait_start_price': None}

    # mark_rollover_pending 方法说明
    # 输入参数：old_contract、new_contract、side、target_lots、stage
    # 输出参数：无（switch_pending=True 并写入移仓合约与阶段）
    # 用途：交割周触发移仓流程时标记待处理
    def mark_rollover_pending(self, *, old_contract: str | None, new_contract: str | None, side: str | None, target_lots: int = 0, stage: str = 'prepare') -> None:
        self.state['switch_pending'] = True
        self.state['switch_old'] = old_contract
        self.state['switch_new'] = new_contract
        self.state['switch_side'] = side
        self.state['switch_target_lots'] = int(target_lots)
        self.state['switch_stage'] = stage

    # update_rollover_stage 方法说明
    # 输入参数：stage（如 prepare/close_old/open_new/done）
    # 输出参数：无（更新 switch_stage）
    # 用途：移仓多步流程阶段推进
    def update_rollover_stage(self, stage: str) -> None:
        self.state['switch_stage'] = stage

    # finalize_rollover 方法说明
    # 输入参数：无
    # 输出参数：无（clear_rollover_flags，保留 roll_carry_points）
    # 用途：移仓全部完成后收尾
    def finalize_rollover(self) -> None:
        self.clear_rollover_flags()
        self.state['roll_carry_points'] = float(self.state.get('roll_carry_points', 0.0) or 0.0)

    # sync_roll_carry_points 方法说明
    # 输入参数：value（移仓累计盈亏点数）
    # 输出参数：无（写入 roll_carry_points）
    # 用途：移仓平旧开新时累计盈亏点传递
    def sync_roll_carry_points(self, value: float) -> None:
        self.state['roll_carry_points'] = float(value)

    # reset_after_close 方法说明
    # 输入参数：current_date、close_reason
    # 输出参数：无（清持仓与移仓标记，保留背离计数与 osc_wait）
    # 用途：平仓后对齐聚宽状态清理
    def reset_after_close(self, *, current_date: str, close_reason: str | None = None) -> None:
        # 对齐聚宽：平仓只清持仓/移仓标记；背离计数与 osc_wait 由 update_counts / 窗口超时更新
        self.clear_position_on_close()
        self.set_day_lock(current_date=current_date)
        self.state['structure_bundle'] = self.state.get('structure_bundle')
        self.state['close_reason'] = close_reason

    # save 方法说明
    # 输入参数：filename（可选，默认 state_path）
    # 输出参数：bool，是否成功写出 JSON
    # 用途：可选将 state 持久化到磁盘（调试/实盘扩展）
    def save(self, filename: str | Path | None = None) -> bool:
        target = Path(filename) if filename is not None else self.state_path
        if target is None:
            return False
        try:
            target.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
            return True
        except Exception:
            return False
