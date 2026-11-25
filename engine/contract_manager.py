from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from .data_portal import DataPortal
from .broker import Broker
from .portfolio import Portfolio
from strategy.state_manager import StateManager


@dataclass
class RolloverDecision:
    # 数据结构说明
    # 输入参数：should_roll（是否移仓），reason（原因），old_contract（旧合约），new_contract（新合约），old_price（旧价格），new_price（新价格），price_mode（取价模式）
    # 输出参数：RolloverDecision 实例
    # 中文映射：
    #   - should_roll -> 是否移仓
    #   - reason -> 原因
    #   - old_contract -> 旧合约
    #   - new_contract -> 新合约
    #   - old_price -> 旧价格
    #   - new_price -> 新价格
    #   - price_mode -> 取价模式
    should_roll: bool
    reason: str = ''
    old_contract: Optional[str] = None
    new_contract: Optional[str] = None
    old_price: Optional[float] = None
    new_price: Optional[float] = None
    price_mode: str = 'asof_nearest'


@dataclass
class RollInstruction:
    roll_id: str
    old_contract: str
    new_contract: str
    side: str
    lots: int
    reason: str = '移仓'
    signal_price: Optional[float] = None
    close_price: Optional[float] = None
    open_price: Optional[float] = None
    stage: str = 'prepare'
    pnl_close: float = 0.0
    pnl_open: float = 0.0
    total_pnl: float = 0.0


class ContractManager:
    # 模块说明
    # 输入参数：data_portal（行情门户），broker（撮合器），portfolio（组合账户），state_manager（状态管理器），cfg（配置，可选）
    # 输出参数：ContractManager 实例
    # 中文映射：
    #   - data_portal -> 行情门户
    #   - broker -> 撮合器
    #   - portfolio -> 组合账户
    #   - state_manager -> 状态管理器
    #   - cfg -> 策略配置
    # 用途：管理IF合约移仓逻辑
    """IF 合约移仓管理。"""

    def __init__(self, data_portal: DataPortal, broker: Broker, portfolio: Portfolio, state_manager: StateManager, cfg: Optional[Any] = None):
        self.data_portal = data_portal
        self.broker = broker
        self.portfolio = portfolio
        self.state_manager = state_manager
        self.cfg = cfg or {}

    # _current_dt 方法说明
    # 输入参数：event（事件对象）
    # 输出参数：pd.Timestamp（当前时间戳）
    # 中文映射：
    #   - event.date -> 交易日期
    #   - event.time_str -> 运行时点
    @staticmethod
    def _current_dt(event) -> pd.Timestamp:
        return pd.Timestamp(f'{event.date.isoformat()} {event.time_str}:00')

    # _third_friday 方法说明
    # 输入参数：year（年份），month（月）
    # 输出参数：pd.Timestamp（当月第三个星期五）
    # 中文映射：
    #   - year -> 年份
    #   - month -> 月份
    def _third_friday(self, year: int, month: int) -> pd.Timestamp:
        first_day = pd.Timestamp(year=year, month=month, day=1)
        offset = (4 - first_day.dayofweek) % 7
        first_friday = first_day + pd.Timedelta(days=offset)
        third_friday = first_friday + pd.Timedelta(days=14)
        return third_friday.normalize()

    # _first_trade_day_on_or_after 方法说明
    # 输入参数：day（日期）
    # 输出参数：pd.Timestamp（不早于该日期的首个交易日）
    # 中文映射：
    #   - day -> 日期
    #   - trade_dates -> 交易日列表
    def _first_trade_day_on_or_after(self, day: pd.Timestamp) -> pd.Timestamp:
        trade_dates = self.data_portal.get_trade_dates('上证指数日线加入实时数据.csv')
        if not trade_dates:
            return day.normalize()
        day = pd.Timestamp(day).normalize()
        for td in trade_dates:
            if td >= day:
                return pd.Timestamp(td).normalize()
        return day

    # _roll_reference_date 方法说明
    # 输入参数：current_dt（当前日期）
    # 输出参数：pd.Timestamp（移仓参考日期）
    # 中文映射：
    #   - current_dt -> 当前日期
    #   - third_friday -> 第三个星期五
    def _delivery_week_bounds(self, year: int, month: int) -> tuple[pd.Timestamp, pd.Timestamp]:
        third_friday = self._third_friday(year, month)
        first_roll = self._first_trade_day_on_or_after(third_friday)
        week_start = first_roll - pd.Timedelta(days=first_roll.dayofweek)
        week_end = week_start + pd.Timedelta(days=6)
        return week_start, week_end

    def _months_to_check_for_roll_week(self, current_dt: pd.Timestamp) -> list[tuple[int, int]]:
        y, m = current_dt.year, current_dt.month
        pairs = [(y, m)]
        if m == 1:
            pairs.append((y - 1, 12))
        else:
            pairs.append((y, m - 1))
        return pairs

    def _roll_reference_date(self, current_dt: pd.Timestamp) -> pd.Timestamp:
        current_dt = pd.Timestamp(current_dt).normalize()
        for check_y, check_m in self._months_to_check_for_roll_week(current_dt):
            week_start, week_end = self._delivery_week_bounds(check_y, check_m)
            if week_start <= current_dt <= week_end:
                return self._third_friday(check_y, check_m)
        y, m = current_dt.year, current_dt.month
        third_friday = self._third_friday(y, m)
        if current_dt <= self._first_trade_day_on_or_after(third_friday):
            return third_friday
        if m == 12:
            return self._third_friday(y + 1, 1)
        return self._third_friday(y, m + 1)

    def _is_roll_week(self, current_dt: pd.Timestamp) -> bool:
        current_dt = pd.Timestamp(current_dt).normalize()
        for check_y, check_m in self._months_to_check_for_roll_week(current_dt):
            week_start, week_end = self._delivery_week_bounds(check_y, check_m)
            if week_start <= current_dt <= week_end:
                return True
        return False

    def _first_roll_day_in_active_week(self, current_dt: pd.Timestamp) -> Optional[pd.Timestamp]:
        current_dt = pd.Timestamp(current_dt).normalize()
        for check_y, check_m in self._months_to_check_for_roll_week(current_dt):
            week_start, week_end = self._delivery_week_bounds(check_y, check_m)
            if week_start <= current_dt <= week_end:
                return self._first_trade_day_on_or_after(self._third_friday(check_y, check_m))
        return None

    def _is_roll_trigger_day(self, current_dt: pd.Timestamp) -> bool:
        current_dt = pd.Timestamp(current_dt).normalize()
        if not self._is_roll_week(current_dt):
            return False
        first_roll = self._first_roll_day_in_active_week(current_dt)
        return first_roll is not None and current_dt >= first_roll

    # _make_roll_id 方法说明
    # 输入参数：current_dt（当前时间），old_contract（旧合约），new_contract（新合约）
    # 输出参数：str（移仓ID）
    def _make_roll_id(self, current_dt: pd.Timestamp, old_contract: str, new_contract: str) -> str:
        return f"{current_dt.strftime('%Y%m%d_%H%M')}_{old_contract}_to_{new_contract}"

    # _contract_month 方法说明
    # 输入参数：contract（合约代码）
    # 输出参数：int | None（月份）
    # 中文映射：
    #   - contract -> 合约代码
    #   - digits -> 数字串
    def _contract_month(contract: str) -> Optional[int]:
        digits = ''.join([c for c in str(contract) if c.isdigit()])
        if len(digits) < 4:
            return None
        try:
            return int(digits[-2:])
        except Exception:
            return None

    @staticmethod
    def _parse_contract_ym(contract: str) -> Optional[tuple[int, int]]:
        digits = ''.join([c for c in str(contract) if c.isdigit()])
        if len(digits) < 4:
            return None
        try:
            return 2000 + int(digits[:2]), int(digits[2:4])
        except Exception:
            return None

    def _pick_roll_target(self, old_contract: str, front: str, nxt: str) -> Optional[str]:
        old_ym = self._parse_contract_ym(old_contract)
        best: Optional[str] = None
        best_ym: Optional[tuple[int, int]] = None
        for candidate in (front, nxt):
            if not candidate:
                continue
            ym = self._parse_contract_ym(candidate)
            if old_ym is not None and ym is not None and ym <= old_ym:
                continue
            if best_ym is None or (ym is not None and ym < best_ym):
                best = candidate
                best_ym = ym
        return best or front or nxt or None

    def _is_expired_contract(self, old_contract: str, front: str, nxt: str) -> bool:
        return old_contract not in (front, nxt)

    # _get_prices 方法说明
    # 输入参数：current_dt（当前时间），old_contract（旧合约），new_contract（新合约）
    # 输出参数：tuple[旧价格, 新价格, 价格模式]
    # 中文映射：
    #   - old_contract -> 旧合约
    #   - new_contract -> 新合约
    #   - old_price -> 旧价格
    #   - new_price -> 新价格
    #   - price_mode -> 取价模式
    def _get_prices(self, current_dt: pd.Timestamp, old_contract: str, new_contract: str) -> tuple[Optional[float], Optional[float], str]:
        row = self.data_portal.get_if_row(current_dt)
        if row is None:
            return None, None, 'missing'
        front = str(row.get('front_contract') or '')
        nxt = str(row.get('next_contract') or '')
        fallback_dt = current_dt.normalize() + pd.Timedelta(hours=14, minutes=30)
        old_price = None
        new_price = None
        if old_contract == front and pd.notna(row.get('front_close')):
            old_price = float(row.get('front_close'))
        elif old_contract == nxt and pd.notna(row.get('next_close')):
            old_price = float(row.get('next_close'))
        if new_contract == front and pd.notna(row.get('front_close')):
            new_price = float(row.get('front_close'))
        elif new_contract == nxt and pd.notna(row.get('next_close')):
            new_price = float(row.get('next_close'))
        if old_price is None:
            old_price, old_mode = self.data_portal.get_if_price_with_fallback(current_dt, old_contract, fallback_dt=fallback_dt)
        else:
            old_mode = 'exact'
        if new_price is None:
            new_price, new_mode = self.data_portal.get_if_price_with_fallback(current_dt, new_contract, fallback_dt=fallback_dt)
        else:
            new_mode = 'exact'
        price_mode = 'exact' if old_mode == 'exact' and new_mode == 'exact' else f'{old_mode}|{new_mode}'
        return old_price, new_price, price_mode

    # build_decision 方法说明
    # 输入参数：event（事件对象）
    # 输出参数：RolloverDecision（移仓决策）
    # 中文映射：
    #   - event.time_str -> 运行时点
    #   - pos -> 当前持仓
    #   - old_contract -> 旧合约
    #   - new_contract -> 新合约
    def build_decision(self, event) -> RolloverDecision:
        if getattr(event, 'time_str', None) != '14:40':
            return RolloverDecision(False, 'not_roll_time')
        pos = self.portfolio.position
        if pos is None or int(getattr(pos, 'amount', 0) or 0) <= 0:
            return RolloverDecision(False, 'no_position')
        now = self._current_dt(event)
        row = self.data_portal.get_if_row(now)
        if row is None:
            return RolloverDecision(False, 'no_if_data')
        old_contract = str(pos.contract or '')
        front = str(row.get('front_contract') or '')
        nxt = str(row.get('next_contract') or '')
        if not front or not nxt:
            return RolloverDecision(False, 'no_contract_candidates')

        if self._is_expired_contract(old_contract, front, nxt):
            new_contract = self._pick_roll_target(old_contract, front, nxt)
            if not new_contract or new_contract == old_contract:
                old_price, _, price_mode = self._get_prices(now, old_contract, front)
                return RolloverDecision(
                    True,
                    'expired_force_close',
                    old_contract=old_contract,
                    new_contract=None,
                    old_price=old_price,
                    new_price=None,
                    price_mode=price_mode,
                )
            old_price, new_price, price_mode = self._get_prices(now, old_contract, new_contract)
            return RolloverDecision(
                True,
                'expired_force_roll',
                old_contract=old_contract,
                new_contract=new_contract,
                old_price=old_price,
                new_price=new_price,
                price_mode=price_mode,
            )

        if old_contract == nxt:
            return RolloverDecision(False, 'already_next_contract')
        if old_contract != front:
            return RolloverDecision(False, 'not_front_contract')
        if not self._is_roll_trigger_day(now):
            return RolloverDecision(False, 'not_roll_week')

        new_contract = nxt
        old_price, new_price, price_mode = self._get_prices(now, old_contract, new_contract)
        return RolloverDecision(True, 'roll_due', old_contract=old_contract, new_contract=new_contract, old_price=old_price, new_price=new_price, price_mode=price_mode)

    # execute_rollover 方法说明
    # 输入参数：event（事件对象）
    # 输出参数：dict（移仓执行结果）
    # 中文映射：
    #   - rolled -> 是否移仓成功
    #   - close_ret -> 旧合约平仓结果
    #   - open_ret -> 新合约开仓结果
    #   - old_contract -> 旧合约
    #   - new_contract -> 新合约
    def execute_rollover(self, event) -> dict:
        decision = self.build_decision(event)
        if not decision.should_roll:
            return {'rolled': False, 'reason': decision.reason}
        if decision.reason == 'expired_force_close':
            return self._execute_force_close(event, decision)

        pos = self.portfolio.position
        sm = self.state_manager.state
        lots = int(getattr(pos, 'amount', 0) or 0)
        side = str(getattr(pos, 'side', '') or '')
        pos_type = getattr(pos, 'position_type', None)
        now = self._current_dt(event)
        roll_id = self._make_roll_id(now, decision.old_contract or '', decision.new_contract or '')

        sm['switch_pending'] = True
        sm['switch_old'] = decision.old_contract
        sm['switch_new'] = decision.new_contract
        sm['switch_side'] = side
        sm['switch_target_lots'] = lots
        sm['switch_stage'] = 'prepare'
        sm['switch_retry_count'] = int(sm.get('switch_retry_count', 0) or 0)

        old_price = decision.old_price
        new_price = decision.new_price
        fallback_dt = now.normalize() + pd.Timedelta(hours=14, minutes=30)
        old_mode = 'exact'
        new_mode = 'exact'
        if old_price is None:
            old_price, old_mode = self.data_portal.get_if_price_with_fallback(now, decision.old_contract, fallback_dt=fallback_dt)
        if new_price is None:
            new_price, new_mode = self.data_portal.get_if_price_with_fallback(now, decision.new_contract, fallback_dt=fallback_dt)
        decision.price_mode = 'exact' if old_mode == 'exact' and new_mode == 'exact' else f'{old_mode}|{new_mode}'

        signal_price = old_price if old_price is not None else float(pos.entry_price or 0.0)
        close_price = old_price if old_price is not None else signal_price
        open_price = new_price if new_price is not None else close_price

        sm['switch_stage'] = 'close_old'
        close_ret = self.broker.close_position(float(close_price))
        if not close_ret.get('closed'):
            failure = '移仓失败：平旧仓未成交'
            self._finalize_switch_state(sm, stage='close_failed')
            return self._build_roll_result(
                rolled=False,
                roll_failed=True,
                failure_reason=failure,
                decision=decision,
                roll_id=roll_id,
                side=side,
                lots=lots,
                signal_price=signal_price,
                close_price=close_price,
                open_price=open_price,
                close_done=False,
                open_done=False,
                close_ret=close_ret,
            )

        pnl_close = float(close_ret.get('pnl', 0.0) or 0.0)
        saved_anchor = sm.get('logical_anchor_entry')
        saved_carry = float(sm.get('roll_carry_points', 0.0) or 0.0)
        saved_top_cnt = sm.get('entry_top_cnt')
        self.state_manager.clear_position_on_rollover()
        sm['logical_anchor_entry'] = saved_anchor
        sm['roll_carry_points'] = saved_carry
        sm['entry_top_cnt'] = saved_top_cnt
        self.state_manager.state['roll_close_pnl'] = pnl_close
        self.state_manager.state['roll_open_pnl'] = 0.0
        self.state_manager.state['roll_total_pnl'] = pnl_close

        sm['switch_stage'] = 'open_new'
        open_ret = self.broker.open_position_with_retry(
            decision.new_contract, side, lots, float(open_price), position_type=pos_type,
        )
        lots = int(open_ret.get('amount') or lots)
        if not open_ret.get('opened'):
            failure = self._describe_roll_open_failure(open_ret, lots, float(open_price))
            self.state_manager.sync_from_portfolio(self.portfolio)
            self._finalize_switch_state(sm, stage='open_failed')
            self.state_manager.state['roll_close_pnl'] = pnl_close
            self.state_manager.state['roll_open_pnl'] = 0.0
            self.state_manager.state['roll_total_pnl'] = pnl_close
            return self._build_roll_result(
                rolled=False,
                roll_failed=True,
                failure_reason=failure,
                decision=decision,
                roll_id=roll_id,
                side=side,
                lots=lots,
                signal_price=signal_price,
                close_price=close_price,
                open_price=open_price,
                close_done=True,
                open_done=False,
                pnl_close=pnl_close,
                close_ret=close_ret,
                open_ret=open_ret,
            )

        pnl_open = 0.0
        sm['roll_carry_points'] = saved_carry + pnl_close

        self.state_manager.sync_from_portfolio(self.portfolio)
        sm['logical_anchor_entry'] = saved_anchor
        sm['roll_carry_points'] = saved_carry + pnl_close
        sm['entry_top_cnt'] = saved_top_cnt
        self._finalize_switch_state(sm, stage='done')
        total_pnl = float(sm['roll_carry_points'])
        self.state_manager.state['roll_close_pnl'] = pnl_close
        self.state_manager.state['roll_open_pnl'] = pnl_open
        self.state_manager.state['roll_total_pnl'] = total_pnl

        return self._build_roll_result(
            rolled=True,
            roll_failed=False,
            decision=decision,
            roll_id=roll_id,
            side=side,
            lots=lots,
            signal_price=signal_price,
            close_price=close_price,
            open_price=open_price,
            close_done=True,
            open_done=True,
            pnl_close=pnl_close,
            pnl_open=pnl_open,
            total_pnl=total_pnl,
            close_ret=close_ret,
            open_ret=open_ret,
        )

    def _execute_force_close(self, event, decision: RolloverDecision) -> dict:
        pos = self.portfolio.position
        if pos is None or int(getattr(pos, 'amount', 0) or 0) <= 0:
            return {'rolled': False, 'reason': 'no_position'}
        now = self._current_dt(event)
        lots = int(getattr(pos, 'amount', 0) or 0)
        side = str(getattr(pos, 'side', '') or '')
        old_contract = str(decision.old_contract or pos.contract or '')
        fallback_dt = now.normalize() + pd.Timedelta(hours=14, minutes=30)
        close_price = decision.old_price
        if close_price is None:
            close_price, _mode = self.data_portal.get_if_price_with_fallback(
                now, old_contract, fallback_dt=fallback_dt
            )
        if close_price is None:
            close_price = float(pos.entry_price or 0.0)
        close_ret = self.broker.close_position(float(close_price))
        if not close_ret.get('closed'):
            return {
                'rolled': False,
                'roll_failed': True,
                'force_closed': False,
                'reason': 'expired_force_close_failed',
                'failure_reason': '过期合约强制平仓未成交',
                'old_contract': old_contract,
                'close_ret': close_ret,
            }
        return {
            'rolled': False,
            'force_closed': True,
            'reason': decision.reason,
            'old_contract': old_contract,
            'side': side,
            'lots': lots,
            'close_price': float(close_price),
            'pnl_close': float(close_ret.get('pnl', 0.0) or 0.0),
            'close_ret': close_ret,
        }

    @staticmethod
    def _finalize_switch_state(sm: dict[str, Any], *, stage: str) -> None:
        sm['switch_pending'] = False
        sm['switch_old'] = None
        sm['switch_new'] = None
        sm['switch_side'] = None
        sm['switch_target_lots'] = 0
        sm['switch_stage'] = stage

    def _describe_roll_open_failure(self, open_ret: dict, lots: int, price: float) -> str:
        reason = str(open_ret.get('reason') or 'unknown')
        if reason == 'insufficient_cash':
            margin = abs(float(price) * int(lots) * 300.0) * 0.15
            commission = float(open_ret.get('commission') or 0.0)
            need = margin + commission
            avail = float(self.portfolio.available_cash or self.portfolio.cash or 0.0)
            return (
                f'移仓失败：开新仓未成交，可用资金不足（可用={avail:.2f}，'
                f'需要保证金+手续费约={need:.2f}，手数={lots}，合约价={price}）'
            )
        return f'移仓失败：开新仓未成交，原因={reason}'

    def _build_roll_result(
        self,
        *,
        rolled: bool,
        roll_failed: bool = False,
        failure_reason: str = '',
        decision: RolloverDecision,
        roll_id: str,
        side: str,
        lots: int,
        signal_price: float,
        close_price: float,
        open_price: float,
        close_done: bool = False,
        open_done: bool = False,
        pnl_close: float = 0.0,
        pnl_open: float = 0.0,
        total_pnl: float = 0.0,
        close_ret: Optional[dict] = None,
        open_ret: Optional[dict] = None,
    ) -> dict:
        return {
            'rolled': rolled,
            'roll_failed': roll_failed,
            'failure_reason': failure_reason,
            'close_done': close_done,
            'open_done': open_done,
            'reason': decision.reason,
            'roll_id': roll_id,
            'price_mode': decision.price_mode,
            'old_contract': decision.old_contract,
            'new_contract': decision.new_contract,
            'side': side,
            'lots': lots,
            'signal_price': signal_price,
            'close_price': close_price,
            'open_price': open_price,
            'pnl_close': pnl_close,
            'pnl_open': pnl_open,
            'total_pnl': total_pnl if rolled or close_done else 0.0,
            'close_ret': close_ret,
            'open_ret': open_ret,
        }
