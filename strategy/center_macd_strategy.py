"""中枢 + MACD 本地回测策略（CenterMacdStrategy）。

输入参数：
  - DataPortal、config（策略参数）、initial_cash、可选 FeatureStore
输出参数：
  - run_log、trades、state_snapshots、equity、summary 等回测产物
用途：
  - 事件驱动回测：9:00 结构冻结 + 运行点 MACD lookup/增量 + 开平仓状态机

使用说明：
  - 由 run_backtest.py / EventEngine 按 RUNPOINTS 调度
  - before_market_open / handle_run_point / roll_contract / after_market_close
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from types import SimpleNamespace
from typing import Any, Optional

from engine.data_portal import DataPortal
from engine.event_engine import Event
from engine.portfolio import Portfolio
from engine.broker import Broker
from engine.contract_manager import ContractManager
from .macd_divergence import MACDDivergenceModule, MacdDivCfg, update_counts_on_divergence
from .state_manager import StateManager
from .structure_module import StructureModule, StructCfg
from .trading_conditions import TradeDecision, TradingConditions, lock_should_apply
from .trigger_detail import format_trigger_detail
from engine.display_format import format_number, format_position_type, format_reason, format_side


@dataclass
class StrategyRuntime:
    initialized: bool = False
    last_event: Optional[str] = None
    last_time: Optional[str] = None
    peak_equity: Optional[float] = None


class CenterMacdStrategy:
    # 模块说明
    # 输入参数：data_portal、config、initial_cash、feature_store（可选）
    # 输出参数：CenterMacdStrategy 实例
    # 用途：中枢 MACD 策略主类，串联结构/MACD/状态机/撮合
    @staticmethod
    def _ensure_config_defaults(cfg: Any) -> Any:
        for dc in (StructCfg(), MacdDivCfg()):
            for f in fields(dc):
                if not hasattr(cfg, f.name):
                    setattr(cfg, f.name, getattr(dc, f.name))
        trading_defaults = {
            'BREAKTHROUGH_WINDOW_BARS': 18,
            'LONG_RANGE_BREAK_TP_PROFIT': 0.05,
            'LONG_RANGE_BREAK_COOLDOWN_DAYS': 3,
            'LONG_RANGE_BREAK_ALLOW_TIMES': ['14:57'],
            'SHORT_RANGE_BREAK_PULLBACK_RATIO': 0.998,
            'SHORT_RANGE_BREAK_PULLBACK_WINDOW_BARS': 10,
            'INDEX_1M_FILE': '上证指数1分钟加入实时数据.csv',
            'LOOKBACK_BARS': 260,
            'OSC_WAIT_BARS': 18,
            'OSC_RULES_MODEL_LONG': 'v1',
            'OSC_RULES_MODEL_SHORT': 'v2',
            'OSC_NEAR_REF_THRESHOLD_LONG': 0.009,
            'OSC_BREAK_REF_THRESHOLD_LONG': 0.015,
            'OSC_NEAR_REF_THRESHOLD_SHORT': 0.005,
            'OSC_BREAK_REF_THRESHOLD_SHORT': 0.01,
            'OSC_NEAR_REF_THRESHOLD': 0.005,
            'OSC_BREAK_REF_THRESHOLD': 0.01,
            'MAX_POSITION_RATIO': 4.0,
        }
        for key, val in trading_defaults.items():
            if not hasattr(cfg, key):
                setattr(cfg, key, val)
        return cfg

    # __init__ 方法说明
    # 输入参数：data_portal、config、initial_cash、feature_store
    # 输出参数：无（初始化各子模块与输出容器）
    # 用途：构造策略实例供 EventEngine 驱动
    def __init__(
        self,
        data_portal: DataPortal,
        config: Optional[Any] = None,
        initial_cash: float = 10_000_000.0,
        feature_store: Optional[Any] = None,
    ):
        self.config = self._ensure_config_defaults(config or SimpleNamespace())
        self.data_portal = data_portal
        self.feature_store = feature_store
        self._debug_state_enabled = bool(
            getattr(self.config, 'DEBUG_STATE', False)
            or os.environ.get('BACKTEST_DEBUG_STATE', '').strip().lower() in ('1', 'true', 'yes')
        )
        self.runtime = StrategyRuntime()
        self.structure = StructureModule(self.config, data_portal=data_portal)
        self.divergence = MACDDivergenceModule(self.config, data_portal=data_portal)
        self.state_manager = StateManager()
        self.portfolio = Portfolio(initial_cash=float(initial_cash))
        self.broker = Broker(self.portfolio)
        self.contract_manager = ContractManager(data_portal, self.broker, self.portfolio, self.state_manager, cfg=self.config)
        self.conditions = TradingConditions(self.config, data_portal=data_portal)
        self.logs: list[dict[str, Any]] = []
        self.run_log: list[dict[str, Any]] = []
        self.trades: list[dict[str, Any]] = []
        self.state_snapshots: list[dict[str, Any]] = []
        self.equity: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {
            'ok': True,
            'events': 0,
            'open_count': 0,
            'close_count': 0,
            'roll_count': 0,
            'trade_count': 0,
            'last_action': None,
            'last_reason': None,
            'last_total_value': self.portfolio.total_value,
            'last_cash': self.portfolio.cash,
            'last_margin_used': self.portfolio.margin_used,
            'last_floating_pnl': self.portfolio.floating_pnl,
            'last_available_cash': self.portfolio.available_cash,
            'total_return': 0.0,
            'last_structure_high': None,
            'last_structure_low': None,
            'last_divergence_has_top': None,
            'last_divergence_has_bottom': None,
            'last_divergence_top_uid': None,
            'last_divergence_bottom_uid': None,
            'last_current_price': None,
            'last_range_high': None,
            'last_range_low': None,
        }

    def _mark_intraday_structure(self, struct: Any) -> Any:
        """盘中结构冻结：9:00 写入 state 后运行点只读，不再重算日线中枢。"""
        if not isinstance(struct, dict):
            return struct
        out = dict(struct)
        out['intraday_frozen'] = True
        out['structure_asof'] = out.get('structure_asof') or 'prev_trade_day_close'
        return out

    def _resolve_div_info_1457(self, as_of: str) -> Optional[dict[str, Any]]:
        """14:57 突破价：当日有 1m 14:57 则用 1m，否则回退 30m（特征表/增量）。"""
        file_1m = getattr(self.config, 'INDEX_1M_FILE', '上证指数1分钟加入实时数据.csv')
        px_1m = self.data_portal.get_index_1m_close_at_1457(file_1m, as_of)
        if px_1m is not None:
            return {
                'divergence': {
                    'has_top': False,
                    'has_bottom': False,
                    'top_detail': None,
                    'bottom_detail': None,
                },
                'current_price': float(px_1m),
                'timestamp': as_of,
                'bar_time': None,
                'close_index': None,
                'close_values': None,
                'has_1m_top': False,
                'macd_mode': '1m_1457',
            }
        if self.feature_store is not None:
            div = self.feature_store.get_div_info(as_of, '14:57')
            if div is not None:
                out = dict(div)
                out['macd_mode'] = '30m_1457_fallback'
                return out
        div = self.divergence.check(as_of, self.data_portal)
        if div is None:
            return None
        out = dict(div)
        out['macd_mode'] = '30m_1457_fallback'
        return out

    def _resolve_div_info(self, as_of: str, time_str: str) -> Optional[dict[str, Any]]:
        """特征表 lookup 优先；缺失时点（如盘中新 bar）走增量 MACD。"""
        if time_str == '14:57':
            return self._resolve_div_info_1457(as_of)
        if self.feature_store is not None:
            div = self.feature_store.get_div_info(as_of, time_str)
            if div is not None:
                return div
        return self.divergence.check(as_of, self.data_portal)

    def _debug_state(self, label: str, event: Event | None = None, extra: Optional[dict[str, Any]] = None) -> None:
        if not self._debug_state_enabled:
            return
        st = self.state_manager.state
        pos = self.portfolio.position
        parts = [
            f"[debug_state] label={label}",
            f"dt={getattr(event, 'dt_str', None)}",
            f"event={getattr(event, 'event_type', None)}",
            f"state_amount={st.get('position_amount')}",
            f"state_contract={st.get('position_contract')}",
            f"state_type={st.get('position_type')}",
            f"state_entry={st.get('entry_price')}",
            f"portfolio_amount={getattr(pos, 'amount', None)}",
            f"portfolio_contract={getattr(pos, 'contract', None)}",
            f"portfolio_side={getattr(pos, 'side', None)}",
            f"portfolio_entry={getattr(pos, 'entry_price', None)}",
            f"cash={self.portfolio.cash}",
            f"available_cash={self.portfolio.available_cash}",
            f"margin_used={self.portfolio.margin_used}",
            f"floating_pnl={self.portfolio.floating_pnl}",
            f"total_value={self.portfolio.total_value}",
        ]
        if extra:
            for k, v in extra.items():
                parts.append(f"{k}={v}")
        print(' | '.join(parts))

    # 模块交互约定
    # 1) structure.build_daily_bundle(as_of)
    #    输入参数：as_of（运行点时间字符串/时间对象）
    #    输出参数：structure bundle（dict）
    #       - center / range_high / range_low / range_state / extend_cnt / strokes / pending_stroke / current_strokes
    #    中文映射：
    #       - as_of -> 运行点时间
    #       - structure bundle -> 日线结构快照
    #       - center -> 中枢
    #       - range_high -> 区间上沿
    #       - range_low -> 区间下沿
    #       - range_state -> 区间状态
    #       - extend_cnt -> 中枢扩展次数
    #       - strokes -> 笔列表
    #       - pending_stroke -> 待确认笔
    #       - current_strokes -> 当前笔序列
    #    用途：作为 09:00 日线结构快照，以及盘中交易条件的结构背景
    # 2) divergence.check(context, dm)
    #    输入参数：context（运行点时间字符串），dm（兼容参数）
    #    输出参数：{'divergence': {...}, 'current_price': float, 'timestamp': ...}
    #       - divergence.has_top / has_bottom / top_detail / bottom_detail
    #    中文映射：
    #       - context -> 运行点时间
    #       - dm -> 兼容数据对象
    #       - divergence -> 背离结果
    #       - current_price -> 当前价格
    #       - timestamp -> 信号时间戳
    #       - has_top -> 顶背离
    #       - has_bottom -> 底背离
    #       - top_detail -> 顶背离详情
    #       - bottom_detail -> 底背离详情
    #    用途：作为 MACD 背离判断与计数更新输入
    # 3) state_manager.state
    #    输入参数：由策略内各模块读写的统一状态字典
    #    输出参数：统一状态字典，承接持仓、背离计数、日内锁、移仓状态等
    #    中文映射：
    #       - position_side -> 持仓方向
    #       - position_type -> 持仓类型
    #       - position_contract -> 持仓合约
    #       - position_amount -> 持仓数量
    #       - day_lock -> 当日禁开锁
    #       - osc_wait -> 震荡等待态
    #       - range_break_wait -> 区间突破等待态
    #       - break_wait -> 突破等待态
    #       - top_divergence_count -> 顶背离计数
    #       - bottom_divergence_count -> 底背离计数
    # 4) broker / portfolio / contract_manager
    #    输入参数：开平仓决策、合约与价格
    #    输出参数：撮合结果、持仓变更、权益变化
    #    中文映射：
    #       - decision -> 交易决策
    #       - contract -> 交易合约
    #       - price -> 成交价格
    #       - lots -> 手数
    #       - pnl -> 盈亏
    #       - margin_used -> 已用保证金
    #       - total_value -> 总权益
    # 5) run_log / trades / state_snapshots / equity / summary
    #    输入参数：事件、交易结果、状态快照
    #    输出参数：回测与验收输出
    #    中文映射：
    #       - run_log -> 运行日志
    #       - trades -> 交易明细
    #       - state_snapshots -> 状态快照
    #       - equity -> 权益曲线
    #       - summary -> 汇总结果
    #    用途：回测与验收输出

    # bootstrap 方法说明
    # 输入参数：无
    # 输出参数：StrategyRuntime（运行时对象）
    # 中文映射：
    #   - initialized -> 已初始化
    #   - runtime -> 运行时对象
    # 用途：标记策略已完成启动
    def bootstrap(self):
        self.runtime.initialized = True
        self.runtime.peak_equity = float(self.portfolio.initial_cash or 0.0)
        return self.runtime

    def _equity_drawdown_metrics(self, total_value: float) -> tuple[float, float]:
        """相对初始：亏损为负、盈利为正；峰值回撤：自历史最高权益回落比例（≤0，创新高为0）。"""
        initial = float(self.portfolio.initial_cash or 0.0)
        tv = float(total_value)
        drawdown = (tv - initial) / initial if initial > 0 else 0.0

        peak = float(self.runtime.peak_equity if self.runtime.peak_equity is not None else initial)
        if peak <= 0:
            peak = initial if initial > 0 else tv
        new_peak = max(peak, tv)
        peak_drawdown = (tv - new_peak) / new_peak if new_peak > 0 else 0.0
        self.runtime.peak_equity = new_peak
        return drawdown, peak_drawdown

    # initialize 方法说明
    # 输入参数：_context（兼容上下文参数）
    # 输出参数：StrategyRuntime（运行时对象）
    # 中文映射：
    #   - _context -> 兼容上下文
    #   - runtime -> 运行时对象
    # 用途：对外初始化入口，保持兼容调用
    def initialize(self, _context: Optional[Any] = None):
        return self.bootstrap()

    # _update_summary 方法说明
    # 输入参数：action（动作类型），reason（原因文本）
    # 输出参数：None
    # 中文映射：
    #   - action -> 动作类型
    #   - reason -> 原因文本
    #   - summary -> 汇总状态
    #   - last_action -> 最近动作
    #   - last_reason -> 最近原因
    # 用途：统一维护汇总统计信息
    def _update_summary(self, action: str, reason: str = '') -> None:
        st = self.summary
        st['last_action'] = action
        st['last_reason'] = reason
        st['last_total_value'] = self.portfolio.total_value
        st['last_action'] = action
        st['last_reason'] = reason
        st['last_total_value'] = self.portfolio.total_value
        st['last_cash'] = self.portfolio.cash
        st['last_margin_used'] = self.portfolio.margin_used
        st['last_floating_pnl'] = self.portfolio.floating_pnl
        st['last_available_cash'] = self.portfolio.available_cash
        if action == 'open':
            st['open_count'] = int(st.get('open_count', 0) or 0) + 1
            st['trade_count'] = int(st.get('trade_count', 0) or 0) + 1
        elif action == 'close':
            st['close_count'] = int(st.get('close_count', 0) or 0) + 1
            st['trade_count'] = int(st.get('trade_count', 0) or 0) + 1
        elif action == 'roll':
            st['roll_count'] = int(st.get('roll_count', 0) or 0) + 1
            st['trade_count'] = int(st.get('trade_count', 0) or 0) + 1
        if self.portfolio.initial_cash:
            st['total_return'] = (float(self.portfolio.total_value) - float(self.portfolio.initial_cash)) / float(self.portfolio.initial_cash)

    # _record 方法说明
    # 输入参数：event（事件对象或事件名称），action（动作类型），reason（原因），extra（附加字段）
    # 输出参数：None
    # 中文映射：
    #   - event -> 事件
    #   - action -> 动作类型
    #   - reason -> 原因文本
    #   - extra -> 附加字段
    #   - run_log -> 运行日志
    #   - logs -> 内部日志列表
    # 用途：统一记录运行日志与汇总动作
    def _record(self, event: Event | str, action: str, reason: str = '', extra: Optional[dict[str, Any]] = None):
        if isinstance(event, Event):
            dt = event.date
            time_str = event.time_str
            event_name = event.event_type
            datetime_str = event.dt_str
        else:
            dt = None
            time_str = None
            event_name = str(event)
            datetime_str = None
        st = self.state_manager.state
        row = {
            'datetime': datetime_str,
            'event': event_name,
            'time': time_str,
            'dt': dt,
            'action': action,
            'reason': reason,
            'signal_price': None,
            'trade_price': None,
            'wait_bars': None,
            'position_amount': st.get('position_amount'),
            'entry_price': st.get('entry_price'),
            'position_type': st.get('position_type'),
            'position_side': st.get('position_side'),
        }
        if extra:
            row.update(extra)
        self.logs.append(row)
        self.run_log.append(row)
        self.summary['events'] = int(self.summary.get('events', 0) or 0) + 1
        self._update_summary(action, reason)

    # _snapshot 方法说明
    # 输入参数：event（事件对象），action（动作类型），reason（原因）
    # 输出参数：None
    # 中文映射：
    #   - state_snapshots -> 状态快照列表
    #   - position_side -> 持仓方向
    #   - position_type -> 持仓类型
    #   - entry_price -> 入场价
    #   - day_lock -> 当日禁开锁
    #   - structure_bundle -> 日线结构快照
    # 用途：记录单次事件对应的状态快照
    def _snapshot(self, event: Event, action: str, reason: str = ''):
        st = self.state_manager.state
        struct = st.get('structure_bundle') or {}
        self.state_snapshots.append({
            'datetime': event.dt_str,
            'event': event.event_type,
            'action': action,
            'reason': reason,
            'position_side': st.get('position_side'),
            'position_contract': st.get('position_contract'),
            'position_amount': st.get('position_amount'),
            'position_type': st.get('position_type'),
            'entry_price': st.get('entry_price'),
            'entry_time': st.get('entry_time'),
            'top_divergence_count': st.get('top_divergence_count'),
            'bottom_divergence_count': st.get('bottom_divergence_count'),
            'day_lock': st.get('day_lock'),
            'switch_pending': st.get('switch_pending'),
            'switch_stage': st.get('switch_stage'),
            'switch_old': st.get('switch_old'),
            'switch_new': st.get('switch_new'),
            'roll_carry_points': st.get('roll_carry_points'),
            'breakout_up_triggered': st.get('breakout_up_triggered'),
            'breakout_down_triggered': st.get('breakout_down_triggered'),
            'range_break_wait': st.get('range_break_wait'),
            'range_high': struct.get('range_high'),
            'range_low': struct.get('range_low'),
            'range_state': struct.get('range_state'),
            'extend_cnt': struct.get('extend_cnt'),
        })

    # _record_trade 方法说明
    # 输入参数：event（事件对象），result（交易结果），decision_reason（决策原因），position_type（持仓类型）
    # 输出参数：None
    # 中文映射：
    #   - result -> 交易结果
    #   - action -> 开仓/平仓/移仓
    #   - side -> 交易方向
    #   - contract -> 交易合约
    #   - price -> 成交价格
    #   - pnl -> 盈亏
    #   - trades -> 交易明细
    # 用途：记录一笔交易明细
    def _resolve_total_pnl(self, *, pnl_amount: Any, skip_total_pnl: bool) -> Optional[float]:
        if skip_total_pnl or pnl_amount is None:
            return None
        carry = float(self.state_manager.state.get('roll_carry_points', 0.0) or 0.0)
        if carry == 0.0:
            return None
        return carry + float(pnl_amount)

    def _record_trade(
        self,
        event: Event,
        result: dict[str, Any],
        decision_reason: str,
        position_type: Optional[str] = None,
        *,
        trigger_extra: Optional[dict[str, Any]] = None,
        skip_total_pnl: bool = False,
    ):
        if not result:
            return
        action = result.get('action')
        if action not in ('open', 'close', 'roll'):
            return
        if action == 'open' and not result.get('opened'):
            return
        if action == 'close' and not result.get('closed'):
            return
        pnl_amount = result.get('pnl')
        trade_price = result.get('price')
        side = result.get('side') or self.state_manager.state.get('position_side')
        pos_type = position_type or result.get('position_type') or self.state_manager.state.get('position_type')
        pnl_ratio = None
        if action == 'close' and pnl_amount is not None:
            anchor = self.state_manager.state.get('logical_anchor_entry') or self.state_manager.state.get('entry_price')
            lots = float(result.get('amount') or self.state_manager.state.get('position_amount') or 1)
            if anchor not in (None, 0) and lots > 0:
                try:
                    pnl_ratio = float(pnl_amount) / (float(anchor) * lots * 300.0)
                except Exception:
                    pnl_ratio = None
        total_pnl = self._resolve_total_pnl(pnl_amount=pnl_amount, skip_total_pnl=skip_total_pnl)
        trigger_detail = format_trigger_detail(
            action=action,
            reason=decision_reason,
            side=side,
            position_type=pos_type,
            extra=trigger_extra,
        )
        row = {
            'datetime': event.dt_str,
            'action': action,
            'side': side,
            'contract': result.get('contract') or self.state_manager.state.get('position_contract'),
            'lots': result.get('amount') or self.state_manager.state.get('position_amount'),
            'price': trade_price,
            'signal_price': result.get('signal_price'),
            'wait_bars': result.get('wait_bars'),
            'commission': result.get('commission'),
            'pnl': pnl_amount,
            'pnl_ratio': pnl_ratio,
            'total_pnl': total_pnl,
            'reason': decision_reason,
            'trigger_detail': trigger_detail,
            'position_type': pos_type,
            'price_mode': result.get('price_mode'),
            'available_cash': self.portfolio.available_cash,
            'margin_used': self.portfolio.margin_used,
            'total_value': self.portfolio.total_value,
        }
        if result.get('extra'):
            row.update({f'extra_{k}': v for k, v in result['extra'].items()})
        self.trades.append(row)

    def _mark_to_market_at_close(self, event: Event) -> Optional[float]:
        st = self.state_manager.state
        pos_amount = int(st.get('position_amount', 0) or 0)
        contract = st.get('position_contract')
        if pos_amount <= 0 or not contract:
            self.portfolio.mark_to_market(None)
            return None
        as_of = f"{event.date.isoformat()} {event.time_str}:00"
        mark_price = self.data_portal.get_if_price(as_of, contract)
        if mark_price is None:
            mark_price, _mode = self.data_portal.get_if_price_with_fallback(
                as_of, contract, fallback_dt=f"{event.date.isoformat()} 14:30:00"
            )
        self.portfolio.mark_to_market(mark_price)
        return mark_price

    def _enrich_close_trigger_extra(self, close_decision, div, struct_snap) -> dict[str, Any]:
        ex = dict(close_decision.extra or {})
        div_state = (div or {}).get('divergence') or {} if isinstance(div, dict) else {}
        ex['has_top'] = bool(div_state.get('has_top'))
        ex['has_bottom'] = bool(div_state.get('has_bottom'))
        ex['day_lock'] = self.state_manager.state.get('day_lock')
        if isinstance(struct_snap, dict):
            if struct_snap.get('range_pct') is not None:
                ex['range_pct'] = struct_snap.get('range_pct')
            ex['center_ready'] = bool(struct_snap.get('center'))
        return ex

    # _calc_open_lots 方法说明
    # 输入参数：contract_price（合约价格）
    # 输出参数：int（开仓手数）
    # 中文映射：
    #   - contract_price -> 合约价格
    #   - lots -> 手数
    #   - total_value -> 总权益
    #   - available_cash -> 可用资金
    # 用途：按资金与保证金约束计算最大可开手数
    def _calc_open_lots(self, contract_price: float) -> int:
        if contract_price is None or contract_price <= 0:
            return 0
        total_value = float(self.portfolio.total_value or self.portfolio.cash or self.portfolio.initial_cash or 0.0)
        available_cash = float(self.portfolio.available_cash or self.portfolio.cash or total_value or 0.0)
        contract_multiplier = Portfolio.CONTRACT_MULTIPLIER
        max_notional = total_value * float(getattr(self.config, 'MAX_POSITION_RATIO', 4.0) or 4.0)
        contract_value = float(contract_price) * contract_multiplier
        if contract_value <= 0:
            return 0
        cash_per_lot = Portfolio._cash_per_lot(contract_price)
        if cash_per_lot <= 0:
            return 0
        lots_by_notional = max_notional / contract_value
        lots_by_cash = available_cash / cash_per_lot
        lots = int(min(lots_by_notional, lots_by_cash))
        return max(1, lots) if lots >= 1 else 0

    def _open_with_lot_retry(
        self,
        contract: str,
        side: str,
        lots: int,
        open_price: float,
        position_type: Optional[str],
    ) -> tuple[dict, int]:
        ret = self.broker.open_position_with_retry(
            contract, side, int(lots), float(open_price), position_type=position_type,
        )
        actual_lots = int(ret.get('amount') or 0)
        return ret, actual_lots

    # before_market_open 方法说明
    # 输入参数：event（运行事件）
    # 输出参数：dict（事件执行结果）
    # 中文映射：
    #   - event -> 事件对象
    #   - event.date -> 交易日期
    #   - event.time_str -> 运行时点
    #   - structure_bundle -> 日线结构快照
    #   - last_structure_high -> 最近结构上沿
    #   - last_structure_low -> 最近结构下沿
    # 用途：完成日内开盘前的状态重置与结构快照构建
    def before_market_open(self, event: Event):
        self.runtime.last_event = event.event_type
        self.runtime.last_time = event.time_str
        self._record(event, 'before_market_open', 'bootstrap daily state')
        self._debug_state('before_market_open', event)
        self.state_manager.reset_daily(event.date.isoformat())
        self.divergence.begin_day(event.date)
        as_of = f"{event.date.isoformat()} {event.time_str}:00"
        if self.feature_store is not None:
            struct = self.feature_store.get_structure_bundle(event.date)
            if struct is None:
                struct = self.structure.build_daily_bundle(as_of)
        else:
            struct = self.structure.build_daily_bundle(as_of)
        struct = self._mark_intraday_structure(struct)
        self.state_manager.state['structure_bundle'] = struct
        self.summary['last_structure_high'] = struct.get('range_high') if isinstance(struct, dict) else None
        self.summary['last_structure_low'] = struct.get('range_low') if isinstance(struct, dict) else None
        if isinstance(struct, dict):
            structure_rows: list[dict[str, Any]] = []
            center = struct.get('center') or {}
            range_row = {
                'kind': 'range',
                'datetime': event.dt_str,
                'event': 'structure_report',
                'action': 'structure_report',
                'reason': 'daily_structure_snapshot',
                'signal_price': None,
                'trade_price': None,
                'wait_bars': None,
                'range_high': struct.get('range_high'),
                'range_low': struct.get('range_low'),
                'range_high_dt': struct.get('range_high_dt'),
                'range_low_dt': struct.get('range_low_dt'),
                'range_pct': struct.get('range_pct'),
                'range_state': struct.get('range_state'),
                'relative_center': '中枢内' if struct.get('center') else '中枢外',
                'range_position': 'inside' if struct.get('range_state') else 'breakout_up' if struct.get('last_close') is not None and struct.get('range_high') is not None and float(struct.get('last_close')) >= float(struct.get('range_high')) else 'breakout_down' if struct.get('last_close') is not None and struct.get('range_low') is not None and float(struct.get('last_close')) <= float(struct.get('range_low')) else 'unknown',
            }
            structure_rows.append(range_row)
            if struct.get('center'):
                center_used_raw = list((struct.get('center') or {}).get('strokes_used') or [])
                center_used = []
                seen_keys = set()
                for s in center_used_raw:
                    key = (s.get('start_dt'), s.get('end_dt'), s.get('dir'))
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    center_used.append(s)
                normalized = []
                normalized_keys = set()
                for s in center_used:
                    key = (s.get('start_dt'), s.get('end_dt'), s.get('dir'), bool(s.get('confirmed', True)))
                    if key in normalized_keys:
                        continue
                    normalized_keys.add(key)
                    normalized.append(s)
                center_used = normalized
                center_row = {
                    'kind': 'center',
                    'datetime': event.dt_str,
                    'event': 'structure_report',
                    'action': 'structure_report',
                    'reason': 'daily_structure_snapshot',
                    'signal_price': None,
                    'trade_price': None,
                    'wait_bars': None,
                    'row_type': 'center',
                    'has_center': True,
                    'ZG': center.get('ZG'),
                    'ZD': center.get('ZD'),
                    'extend_cnt': struct.get('extend_cnt'),
                    'start_dt': center.get('start_dt'),
                    'end_dt': center.get('end_dt'),
                    'high_dt': struct.get('range_high_dt'),
                    'low_dt': struct.get('range_low_dt'),
                }
                structure_rows.append(center_row)
                strokes = center_used if center_used else list(struct.get('current_strokes') or [])[-3:]
            else:
                structure_rows.append({
                    'kind': 'center',
                    'datetime': event.dt_str,
                    'event': 'structure_report',
                    'action': 'structure_report',
                    'reason': 'daily_structure_snapshot',
                    'signal_price': None,
                    'trade_price': None,
                    'wait_bars': None,
                    'row_type': 'center',
                    'has_center': False,
                })
                strokes = list(struct.get('current_strokes') or [])[-3:]
            for s in strokes:
                structure_rows.append({
                    'kind': 'stroke',
                    'datetime': event.dt_str,
                    'event': 'structure_report',
                    'action': 'structure_report',
                    'reason': 'daily_structure_snapshot',
                    'signal_price': None,
                    'trade_price': None,
                    'wait_bars': None,
                    'row_type': 'stroke',
                    'idx': s.get('idx'),
                    'dir': s.get('dir'),
                    'start_dt': s.get('start_dt'),
                    'end_dt': s.get('end_dt'),
                    'high': s.get('high'),
                    'low': s.get('low'),
                    'start_p': s.get('start_p'),
                    'end_p': s.get('end_p'),
                    'confirmed': s.get('confirmed'),
                    'high_dt': s.get('high_dt'),
                    'low_dt': s.get('low_dt'),
                    'pivot_confirm_dt': s.get('pivot_confirm_dt'),
                })
            for row in structure_rows:
                self.run_log.append(row)
                self.logs.append(row)
        self.state_manager.state['structure_bundle'] = struct if isinstance(struct, dict) else None
        self.summary['last_structure_high'] = struct.get('range_high') if isinstance(struct, dict) else None
        self.summary['last_structure_low'] = struct.get('range_low') if isinstance(struct, dict) else None
        self.summary['last_range_high'] = struct.get('range_high') if isinstance(struct, dict) else None
        self.summary['last_range_low'] = struct.get('range_low') if isinstance(struct, dict) else None
        self.summary['last_divergence_has_top'] = None
        self.summary['last_divergence_has_bottom'] = None
        self.summary['last_divergence_top_uid'] = None
        self.summary['last_divergence_bottom_uid'] = None
        self._snapshot(event, 'before_market_open')
        return {'event': event.event_type, 'time': event.time_str, 'action': 'before_market_open', 'structure_bundle': struct}

    # handle_run_point 方法说明
    # 输入参数：event（运行事件）
    # 输出参数：dict（运行点执行结果）
    # 中文映射：
    #   - event -> 事件对象
    #   - as_of -> 运行点时间
    #   - div -> 背离结果
    #   - market_status -> 行情状态
    #   - close_decision -> 平仓决策
    #   - open_decision -> 开仓决策
    #   - run_log -> 运行日志
    #   - trades -> 交易明细
    #   - state_snapshots -> 状态快照
    # 用途：完成运行点的背离更新、开平仓判断与状态写回
    def handle_run_point(self, event: Event):
        self.runtime.last_event = event.event_type
        self.runtime.last_time = event.time_str
        self._record(event, 'run_point', event.time_str)
        as_of = f"{event.date.isoformat()} {event.time_str}:00"
        div = self._resolve_div_info(as_of, event.time_str)
        if div and div.get('divergence') and event.time_str != '14:57':
            update_counts_on_divergence(div, self.state_manager.state, self.config)

        struct_snap = self.state_manager.state.get('structure_bundle')
        current_price = div.get('current_price') if div is not None else None

        # 价格回到震荡区间内部时，重置 breakout 防重复触发
        if current_price is not None and isinstance(struct_snap, dict):
            rh = struct_snap.get('range_high')
            rl = struct_snap.get('range_low')
            if rh is not None and rl is not None:
                px = float(current_price)
                if (px > float(rl)) and (px < float(rh)):
                    self.state_manager.state['breakout_up_triggered'] = False
                    self.state_manager.state['breakout_down_triggered'] = False
        market_status = {'current_price': current_price, 'timestamp': as_of}
        if int(getattr(self.portfolio.position, 'amount', 0) or 0) > 0:
            contract = self.state_manager.state.get('position_contract') or getattr(self.portfolio.position, 'contract', None)
            if contract:
                contract_price = self.data_portal.get_if_price(as_of, contract)
                if contract_price is None:
                    contract_price, _mode = self.data_portal.get_if_price_with_fallback(
                        as_of, contract, fallback_dt=f"{event.date.isoformat()} 14:30:00"
                    )
                if contract_price is not None:
                    market_status['trade_price'] = float(contract_price)
        if div and div.get('divergence'):
            d = div['divergence']
            self.summary['last_divergence_has_top'] = bool(d.get('has_top'))
            self.summary['last_divergence_has_bottom'] = bool(d.get('has_bottom'))
            self.summary['last_divergence_top_uid'] = (d.get('top_detail') or {}).get('curr_band_uid')
            self.summary['last_divergence_bottom_uid'] = (d.get('bottom_detail') or {}).get('curr_band_uid')
            self.summary['last_current_price'] = current_price
            self.summary['last_range_high'] = struct_snap.get('range_high') if isinstance(struct_snap, dict) else None
            self.summary['last_range_low'] = struct_snap.get('range_low') if isinstance(struct_snap, dict) else None
        self.run_log[-1].update({
            'current_price': current_price,
            'signal_price': current_price,
            'trade_price': market_status.get('trade_price') if isinstance(market_status, dict) else None,
            'wait_bars': (self.state_manager.state.get('osc_wait') or {}).get('long', {}).get('valid_bars') if self.state_manager.state.get('osc_wait') else None,
            'range_high': struct_snap.get('range_high') if isinstance(struct_snap, dict) else None,
            'range_low': struct_snap.get('range_low') if isinstance(struct_snap, dict) else None,
            'range_state': struct_snap.get('range_state') if isinstance(struct_snap, dict) else None,
            'top_divergence_count': self.state_manager.state.get('top_divergence_count'),
            'bottom_divergence_count': self.state_manager.state.get('bottom_divergence_count'),
            'position_side': self.state_manager.state.get('position_side'),
            'position_type': self.state_manager.state.get('position_type'),
            'position_contract': self.state_manager.state.get('position_contract'),
            'position_amount': self.state_manager.state.get('position_amount'),
            'day_lock': self.state_manager.state.get('day_lock'),
            'switch_pending': self.state_manager.state.get('switch_pending'),
            'switch_stage': self.state_manager.state.get('switch_stage'),
        })
        if event.time_str and event.time_str[3:] in ('00', '30'):
            has_top = bool(div.get('divergence', {}).get('has_top')) if isinstance(div, dict) and div.get('divergence') else False
            has_bottom = bool(div.get('divergence', {}).get('has_bottom')) if isinstance(div, dict) and div.get('divergence') else False
            top_count = int(self.state_manager.state.get('top_divergence_count', 0) or 0)
            bottom_count = int(self.state_manager.state.get('bottom_divergence_count', 0) or 0)
            pos_type = self.state_manager.state.get('position_type')
            has_pos = bool(self.state_manager.state.get('position_amount', 0) and int(self.state_manager.state.get('position_amount', 0)) > 0)
            day_lock = self.state_manager.state.get('day_lock') or {}
            contract = self.state_manager.state.get('position_contract')
            floating_pnl = self.portfolio.floating_pnl
            state_line = (
                f"{event.dt_str} - INFO  -  顶背离={'是' if has_top else '否'} | 底背离={'是' if has_bottom else '否'} | 顶计数={top_count} | 底计数={bottom_count} | "
                f"持仓类型={format_position_type(pos_type)} | 合约={contract} | 浮动盈亏={format_number(floating_pnl)} | "
                f"当日禁多={'是' if day_lock.get('ban_long') else '否'} | 当日禁空={'是' if day_lock.get('ban_short') else '否'} | 有持仓={'是' if has_pos else '否'}"
            )
            self._record(event, 'run_point_state', 'half_hour_state_report', extra={
                'state_line': state_line,
                'has_top': has_top,
                'has_bottom': has_bottom,
                'top_count': top_count,
                'bottom_count': bottom_count,
                'pos_type': pos_type,
                'position_contract': contract,
                'floating_pnl': floating_pnl,
                'ban_long': bool(day_lock.get('ban_long')),
                'ban_short': bool(day_lock.get('ban_short')),
                'has_pos': has_pos,
            })

        # 先平后开
        pos = self.portfolio.position
        self._debug_state('run_point_start', event, extra={'time_str': event.time_str})
        long_break_times = getattr(self.config, 'LONG_RANGE_BREAK_ALLOW_TIMES', ['14:57']) or ['14:57']
        skip_close = event.time_str in long_break_times and bool(pos and int(getattr(pos, 'amount', 0) or 0) > 0)
        close_decision = TradeDecision(False, reason='skipped_at_long_break_time') if skip_close else self.conditions.check_close(
            market_status, div, self.state_manager, pos, struct_snap=struct_snap, time_str=event.time_str,
        )
        if close_decision.should_trade and close_decision.action == 'close':
            close_price = market_status.get('trade_price') if isinstance(market_status, dict) and market_status.get('trade_price') is not None else None
            if close_price is None and self.state_manager.state.get('position_contract'):
                close_price = self.data_portal.get_if_price(as_of, self.state_manager.state.get('position_contract'))
            if close_price is None:
                close_price, _mode = self.data_portal.get_if_price_with_fallback(as_of, self.state_manager.state.get('position_contract') or '', fallback_dt=f"{event.date.isoformat()} 14:30:00")
            if close_price is None:
                close_price = float(getattr(pos, 'entry_price', 0.0) or 0.0)
            ret = self.broker.close_position(float(close_price))
            ret = dict(ret)
            ret['price'] = close_price
            ret['trade_price'] = close_price
            ret['signal_price'] = current_price
            ret['wait_bars'] = None
            ret['side'] = close_decision.side
            ret['position_type'] = close_decision.position_type
            if ret.get('closed'):
                self._record_trade(
                    event,
                    ret,
                    close_decision.reason,
                    position_type=close_decision.position_type,
                    trigger_extra=self._enrich_close_trigger_extra(close_decision, div, struct_snap),
                )
                self.state_manager.clear_position()
                self.state_manager.reset_after_close(current_date=event.date.isoformat(), close_reason=close_decision.reason)
                self._debug_state('after_close', event, extra={'reason': close_decision.reason})
                self.state_manager.state['cash'] = self.portfolio.cash
                self.state_manager.state['available_cash'] = self.portfolio.available_cash
                self.state_manager.state['margin_used'] = self.portfolio.margin_used
                self.state_manager.state['floating_pnl'] = self.portfolio.floating_pnl
                self.state_manager.state['total_value'] = self.portfolio.total_value
                if lock_should_apply(close_decision.reason):
                    if close_decision.side == 'long':
                        self.state_manager.ban_long_today(event.date.isoformat(), close_decision.reason)
                    elif close_decision.side == 'short':
                        self.state_manager.ban_short_today(event.date.isoformat(), close_decision.reason)
                if (
                    close_decision.position_type == 'PT_LONG_RANGE_BREAK'
                    and 'take_profit' in str(close_decision.reason)
                ):
                    end_date = self.conditions.calc_cooldown_end_date(
                        event.date.isoformat(),
                        days=int(getattr(self.config, 'LONG_RANGE_BREAK_COOLDOWN_DAYS', 3) or 3),
                    )
                    self.state_manager.state['long_range_break_cooldown_start_date'] = event.date.isoformat()
                    self.state_manager.state['long_range_break_cooldown_end_date'] = end_date
                self._record(event, 'close', f"{close_decision.reason} | {ret}", extra={'signal_price': current_price, 'trade_price': close_price, 'wait_bars': None})
            else:
                self._record(event, 'close_failed', f"平仓未成交 | {ret}", extra={'signal_price': current_price, 'trade_price': close_price})
            pos = self.portfolio.position

        open_decision = self.conditions.check_open(
            as_of, market_status, div, self.state_manager, event.time_str,
            struct_snap=struct_snap, current_date=event.date.isoformat(),
        )
        self._record(event, 'open_decision', open_decision.reason, extra={
            'should_trade': open_decision.should_trade,
            'decision_action': open_decision.action,
            'decision_side': open_decision.side,
            'decision_position_type': open_decision.position_type,
            'decision_reason': open_decision.reason,
            'decision_contract_mode': open_decision.contract_mode,
            'decision_extra': open_decision.extra,
            'current_price': current_price,
            'range_high': struct_snap.get('range_high') if isinstance(struct_snap, dict) else None,
            'range_low': struct_snap.get('range_low') if isinstance(struct_snap, dict) else None,
            'range_state': struct_snap.get('range_state') if isinstance(struct_snap, dict) else None,
            'top_divergence_count': self.state_manager.state.get('top_divergence_count'),
            'bottom_divergence_count': self.state_manager.state.get('bottom_divergence_count'),
            'position_side': self.state_manager.state.get('position_side'),
            'position_type': self.state_manager.state.get('position_type'),
            'position_contract': self.state_manager.state.get('position_contract'),
            'position_amount': self.state_manager.state.get('position_amount'),
        })
        has_portfolio_pos = bool(self.portfolio.position and int(getattr(self.portfolio.position, 'amount', 0) or 0) > 0)
        has_state_pos = bool(self.state_manager.state.get('position_amount', 0) and int(self.state_manager.state.get('position_amount', 0)) > 0)
        if open_decision.should_trade and open_decision.action == 'open' and not has_portfolio_pos and not has_state_pos:
            side = open_decision.side or 'long'
            position_type = open_decision.position_type
            front, nxt = self.data_portal.get_if_contracts(as_of)
            contract = nxt or front
            open_price = None
            if contract:
                open_price = self.data_portal.get_if_price(as_of, contract)
                if open_price is None:
                    open_price, _mode = self.data_portal.get_if_price_with_fallback(as_of, contract, fallback_dt=f"{event.date.isoformat()} 14:30:00")
            self._record(event, 'open_precheck', 'before_broker_open', extra={
                'decision_reason': open_decision.reason,
                'contract': contract,
                'open_price': open_price,
                'lots_candidate': self._calc_open_lots(float(open_price)) if open_price is not None else 0,
            })
            if contract and open_price is not None:
                lots = self._calc_open_lots(float(open_price))
                if lots > 0:
                    ret, actual_lots = self._open_with_lot_retry(
                        contract, side, lots, float(open_price), position_type,
                    )
                    ret['price'] = float(open_price)
                    ret['trade_price'] = float(open_price)
                    ret['signal_price'] = current_price
                    _open_extra = open_decision.extra or {}
                    ret['wait_bars'] = _open_extra.get('wait_bars')
                    if ret['wait_bars'] is None and _open_extra.get('bars_elapsed') is not None:
                        ret['wait_bars'] = _open_extra.get('bars_elapsed')
                    ret['side'] = side
                    ret['position_type'] = position_type
                    if not ret.get('opened'):
                        fail_reason = ret.get('reason') or 'unknown'
                        requested = int(ret.get('requested_amount') or lots)
                        if fail_reason == 'insufficient_cash':
                            msg = (
                                f"开仓失败：可用资金不足（可用={self.portfolio.available_cash:.2f}，"
                                f"请求手数={requested}，合约价={open_price}）"
                            )
                        else:
                            msg = f"开仓失败：原因={fail_reason}"
                        self._record(event, 'open_failed', msg, extra={
                            'decision_reason': open_decision.reason,
                            'contract': contract,
                            'open_price': open_price,
                            'lots': requested,
                        })
                    else:
                        self.state_manager.set_position(
                            side=side,
                            position_type=position_type,
                            contract=contract,
                            amount=actual_lots,
                            entry_price=float(open_price),
                            entry_time=as_of,
                            entry_top_cnt=self.state_manager.state.get('top_divergence_count'),
                            logical_anchor_entry=float(open_price),
                        )
                        if position_type == 'PT_LONG_RANGE_BREAK':
                            self.state_manager.state['breakout_up_triggered'] = True
                        elif position_type == 'PT_SHORT_RANGE_BREAK':
                            self.state_manager.state['breakout_down_triggered'] = True
                        self._record_trade(
                            event,
                            ret,
                            open_decision.reason,
                            position_type=position_type,
                            trigger_extra=open_decision.extra,
                            skip_total_pnl=True,
                        )
                        self._debug_state('after_open', event, extra={
                            'reason': open_decision.reason,
                            'contract': contract,
                            'lots': actual_lots,
                            'requested_lots': ret.get('requested_amount', lots),
                        })
                        self.state_manager.state['cash'] = self.portfolio.cash
                        self.state_manager.state['available_cash'] = self.portfolio.available_cash
                        self.state_manager.state['margin_used'] = self.portfolio.margin_used
                        self.state_manager.state['floating_pnl'] = self.portfolio.floating_pnl
                        self.state_manager.state['total_value'] = self.portfolio.total_value
                        self._record(event, 'open', f"{open_decision.reason} | {ret}", extra={
                            'current_price': current_price,
                            'range_high': struct_snap.get('range_high') if isinstance(struct_snap, dict) else None,
                            'range_low': struct_snap.get('range_low') if isinstance(struct_snap, dict) else None,
                            'range_state': struct_snap.get('range_state') if isinstance(struct_snap, dict) else None,
                            'top_divergence_count': self.state_manager.state.get('top_divergence_count'),
                            'bottom_divergence_count': self.state_manager.state.get('bottom_divergence_count'),
                            'position_side': self.state_manager.state.get('position_side'),
                            'position_type': self.state_manager.state.get('position_type'),
                            'position_contract': self.state_manager.state.get('position_contract'),
                            'position_amount': self.state_manager.state.get('position_amount'),
                            'lots': lots,
                        })
                else:
                    self._record(event, 'open_blocked', 'lots_not_positive', extra={
                        'decision_reason': open_decision.reason,
                        'contract': contract,
                        'open_price': open_price,
                    })
            else:
                self._record(event, 'open_blocked', 'missing_contract_or_price', extra={
                    'decision_reason': open_decision.reason,
                    'contract': contract,
                    'open_price': open_price,
                })

        self._snapshot(event, 'run_point')
        return {'event': event.event_type, 'time': event.time_str, 'action': 'run_point', 'divergence': div}

    # roll_contract_if_needed 方法说明
    # 输入参数：event（运行事件）
    # 输出参数：dict（移仓执行结果）
    # 中文映射：
    #   - event -> 事件对象
    #   - contract_manager -> 合约管理器
    #   - result -> 移仓结果
    #   - rolled -> 是否完成移仓
    #   - old_contract -> 旧合约
    #   - new_contract -> 新合约
    #   - old_price -> 旧合约价格
    #   - new_price -> 新合约价格
    # 用途：在指定时点检查是否需要移仓，并写回移仓状态
    def roll_contract_if_needed(self, event: Event):
        self.runtime.last_event = event.event_type
        self.runtime.last_time = event.time_str
        result = self.contract_manager.execute_rollover(event)
        if result.get('force_closed'):
            close_ret = dict(result.get('close_ret') or {})
            reason = '过期合约强制平仓'
            pos_type = self.state_manager.state.get('position_type')
            self._record(event, 'force_close', reason, extra={
                'old_contract': result.get('old_contract'),
                'side': result.get('side'),
                'lots': result.get('lots'),
                'trade_price': result.get('close_price'),
                'pnl': result.get('pnl_close'),
            })
            self._record_trade(
                event,
                {
                    'action': 'close',
                    'closed': True,
                    'side': result.get('side'),
                    'contract': result.get('old_contract'),
                    'amount': result.get('lots'),
                    'price': result.get('close_price'),
                    'pnl': result.get('pnl_close'),
                    'commission': close_ret.get('commission'),
                    'position_type': pos_type,
                },
                reason,
                position_type=pos_type,
                trigger_extra={'branch': 'expired_force_close'},
            )
            self.state_manager.clear_position_on_close()
            self.state_manager.reset_after_close(current_date=event.date.isoformat(), close_reason=reason)
            self.state_manager.state['cash'] = self.portfolio.cash
            self.state_manager.state['available_cash'] = self.portfolio.available_cash
            self.state_manager.state['margin_used'] = self.portfolio.margin_used
            self.state_manager.state['floating_pnl'] = self.portfolio.floating_pnl
            self.state_manager.state['total_value'] = self.portfolio.total_value
            self._snapshot(event, 'roll_contract', result.get('reason', reason))
            return {'event': event.event_type, 'time': event.time_str, 'action': 'roll_contract', 'result': result}

        if not result.get('rolled') and not result.get('roll_failed'):
            return {'event': event.event_type, 'time': event.time_str, 'action': 'roll_contract', 'result': result}

        roll_id = result.get('roll_id')
        common_extra = {
            'roll_id': roll_id,
            'old_contract': result.get('old_contract'),
            'new_contract': result.get('new_contract'),
            'side': result.get('side'),
            'lots': result.get('lots'),
            'signal_price': result.get('signal_price'),
            'close_price': result.get('close_price'),
            'open_price': result.get('open_price'),
            'price_mode': result.get('price_mode'),
        }

        if result.get('roll_failed'):
            failure_reason = result.get('failure_reason') or '移仓失败'
            self._record(event, 'roll_failed', failure_reason, extra={
                **common_extra,
                'close_done': result.get('close_done'),
                'open_done': result.get('open_done'),
                'pnl_close': result.get('pnl_close'),
            })
            if result.get('close_done'):
                close_ret = dict(result.get('close_ret') or {})
                self._record(event, 'roll close', '移仓', extra={
                    **common_extra,
                    'trade_price': result.get('close_price'),
                    'stage': 'close',
                    'pnl_close': result.get('pnl_close'),
                    'total_pnl': result.get('pnl_close'),
                })
                self._record_trade(
                    event,
                    {
                        'action': 'close',
                        'closed': True,
                        'side': result.get('side'),
                        'contract': result.get('old_contract'),
                        'amount': result.get('lots'),
                        'price': result.get('close_price'),
                        'signal_price': result.get('signal_price'),
                        'price_mode': result.get('price_mode'),
                        'pnl': result.get('pnl_close'),
                        'commission': close_ret.get('commission'),
                        'extra': {'roll_id': roll_id, 'new_contract': result.get('new_contract'), 'roll_open_failed': True},
                    },
                    '移仓',
                    trigger_extra={'branch': 'roll_close', 'roll_open_failed': True},
                    skip_total_pnl=True,
                )
            self.state_manager.finalize_rollover()
            self.state_manager.state['cash'] = self.portfolio.cash
            self.state_manager.state['available_cash'] = self.portfolio.available_cash
            self.state_manager.state['margin_used'] = self.portfolio.margin_used
            self.state_manager.state['floating_pnl'] = self.portfolio.floating_pnl
            self.state_manager.state['total_value'] = self.portfolio.total_value
            self._snapshot(event, 'roll_contract', failure_reason)
            return {'event': event.event_type, 'time': event.time_str, 'action': 'roll_contract', 'result': result}

        self._record(event, 'roll_contract', 'roll check')
        self.state_manager.update_rollover_stage('close_done')

        self._record(event, 'roll close', '移仓', extra={
            **common_extra,
            'trade_price': result.get('close_price'),
            'stage': 'close',
            'pnl_close': result.get('pnl_close'),
            'pnl_open': 0.0,
            'total_pnl': result.get('pnl_close'),
        })
        close_ret = dict(result.get('close_ret') or {})
        self._record_trade(
            event,
            {
                'action': 'close',
                'closed': True,
                'side': result.get('side'),
                'contract': result.get('old_contract'),
                'amount': result.get('lots'),
                'price': result.get('close_price'),
                'signal_price': result.get('signal_price'),
                'price_mode': result.get('price_mode'),
                'pnl': result.get('pnl_close'),
                'commission': close_ret.get('commission'),
                'extra': {'roll_id': roll_id, 'new_contract': result.get('new_contract')},
            },
            '移仓',
            trigger_extra={'branch': 'roll_close', 'pnl_close': result.get('pnl_close')},
            skip_total_pnl=True,
        )

        self._record(event, 'roll open', '移仓', extra={
            **common_extra,
            'trade_price': result.get('open_price'),
            'stage': 'open',
            'pnl_close': result.get('pnl_close'),
            'pnl_open': result.get('pnl_open'),
            'total_pnl': result.get('total_pnl'),
        })
        open_ret = dict(result.get('open_ret') or {})
        self._record_trade(
            event,
            {
                'action': 'open',
                'opened': True,
                'side': result.get('side'),
                'contract': result.get('new_contract'),
                'amount': result.get('lots'),
                'price': result.get('open_price'),
                'signal_price': result.get('signal_price'),
                'price_mode': result.get('price_mode'),
                'pnl': result.get('pnl_open'),
                'commission': open_ret.get('commission'),
                'position_type': open_ret.get('position_type'),
                'extra': {'roll_id': roll_id, 'old_contract': result.get('old_contract')},
            },
            '移仓',
            position_type=open_ret.get('position_type'),
            trigger_extra={'branch': 'roll_open', 'old_contract': result.get('old_contract'), 'new_contract': result.get('new_contract')},
            skip_total_pnl=True,
        )

        self.state_manager.state['roll_close_pnl'] = result.get('pnl_close')
        self.state_manager.state['roll_open_pnl'] = result.get('pnl_open')
        self.state_manager.state['roll_total_pnl'] = result.get('total_pnl')
        self._debug_state('after_roll', event, extra={'roll_id': roll_id, 'reason': result.get('reason')})
        self.state_manager.state['cash'] = self.portfolio.cash
        self.state_manager.state['available_cash'] = self.portfolio.available_cash
        self.state_manager.state['margin_used'] = self.portfolio.margin_used
        self.state_manager.state['floating_pnl'] = self.portfolio.floating_pnl
        self.state_manager.state['total_value'] = self.portfolio.total_value
        self.state_manager.update_rollover_stage('done')
        self.state_manager.finalize_rollover()
        self._snapshot(event, 'roll_contract', result.get('reason', ''))
        return {'event': event.event_type, 'time': event.time_str, 'action': 'roll_contract', 'result': result}

    # after_market_close 方法说明
    # 输入参数：event（运行事件）
    # 输出参数：dict（收盘后执行结果）
    # 中文映射：
    #   - event -> 事件对象
    #   - portfolio -> 组合账户
    #   - position_amount -> 持仓数量
    #   - entry_price -> 入场价
    #   - position_type -> 持仓类型
    #   - equity -> 权益曲线
    #   - summary -> 汇总结果
    # 用途：在收盘后同步持仓、记录权益、输出状态快照
    def after_market_close(self, event: Event):
        self.runtime.last_event = event.event_type
        self.runtime.last_time = event.time_str
        if self._debug_state_enabled:
            print(
                f"[debug_before_close] dt={event.dt_str} | portfolio_amount={getattr(self.portfolio.position, 'amount', None)} | portfolio_contract={getattr(self.portfolio.position, 'contract', None)} | state_amount={self.state_manager.state.get('position_amount')} | state_contract={self.state_manager.state.get('position_contract')}"
            )
        self.state_manager.sync_from_portfolio(self.portfolio)
        st = self.state_manager.state
        pos_amount = int(st.get('position_amount', 0) or 0)
        entry_price = st.get('entry_price')
        position_type = st.get('position_type')
        position_reason = st.get('close_reason') or st.get('position_type')
        mark_price = self._mark_to_market_at_close(event)
        drawdown, peak_drawdown = self._equity_drawdown_metrics(float(self.portfolio.total_value))
        self.equity.append({
            'datetime': event.dt_str,
            'cash': self.portfolio.cash,
            'available_cash': self.portfolio.available_cash,
            'margin_used': self.portfolio.margin_used,
            'floating_pnl': self.portfolio.floating_pnl,
            'total_value': self.portfolio.total_value,
            'mark_price': mark_price,
            'position_contract': st.get('position_contract'),
            'position_amount': pos_amount,
            'logical_anchor_entry': st.get('logical_anchor_entry'),
            'roll_carry_points': st.get('roll_carry_points'),
            'drawdown': drawdown,
            'peak_drawdown': peak_drawdown,
        })
        self.summary['last_total_value'] = self.portfolio.total_value
        self.summary['last_cash'] = self.portfolio.cash
        self.summary['last_margin_used'] = self.portfolio.margin_used
        self.summary['last_floating_pnl'] = self.portfolio.floating_pnl
        self.summary['last_available_cash'] = self.portfolio.available_cash
        self.summary['last_structure_high'] = None
        self.summary['last_structure_low'] = None
        self.summary['last_range_high'] = None
        self.summary['last_range_low'] = None
        self.summary['last_divergence_has_top'] = None
        self.summary['last_divergence_has_bottom'] = None
        self.summary['last_divergence_top_uid'] = None
        self.summary['last_divergence_bottom_uid'] = None
        self.summary['last_current_price'] = None
        self.state_manager.state['structure_bundle'] = None
        self._record(event, 'after_market_close', 'persist state', extra={
            'position_amount': pos_amount,
            'entry_price': entry_price,
            'position_type': position_type,
            'position_reason': position_reason,
            'position_contract': st.get('position_contract'),
            'floating_pnl': self.portfolio.floating_pnl,
            'mark_price': mark_price,
            'cash': self.portfolio.cash,
            'available_cash': self.portfolio.available_cash,
            'margin_used': self.portfolio.margin_used,
            'total_value': self.portfolio.total_value,
            'logical_anchor_entry': st.get('logical_anchor_entry'),
            'roll_carry_points': st.get('roll_carry_points'),
        })
        print(
            f"{event.dt_str} - INFO  -  [收盘] 持仓合约={st.get('position_contract')} 持仓手数={pos_amount} "
            f"入场价={format_number(entry_price)} 锚定价={format_number(st.get('logical_anchor_entry'))} 收盘估价={format_number(mark_price)} "
            f"浮动盈亏={format_number(self.portfolio.floating_pnl)} 总权益={format_number(self.portfolio.total_value)} "
            f"可用资金={format_number(self.portfolio.available_cash)} 占用保证金={format_number(self.portfolio.margin_used)}"
        )
        self._snapshot(event, 'after_market_close')
        return {'event': event.event_type, 'time': event.time_str, 'action': 'after_market_close'}
