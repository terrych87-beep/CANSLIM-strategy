"""回测输出中文格式化与表头翻译。

输入参数：
  - 原始字段值（side、reason、position_type 等）或行 dict
输出参数：
  - 格式化后的显示值 / 行 dict / 中文表头列表
用途：
  - ReportWriter、日志、网页展示的统一中文映射与数值格式

使用说明：
  - format_trade_row / format_equity_row / format_state_row 供 CSV 写出前调用
  - translate_headers 将英文字段名转为中文列名
"""

from __future__ import annotations

from typing import Any, Optional

SIDE_ZH = {'long': '多', 'short': '空'}
ACTION_ZH = {'open': '开', 'close': '平'}
EVENT_ZH = {
    'before_market_open': '盘前',
    'run_point': '运行点',
    'run_point_special': '运行点(14:57)',
    'roll_contract': '移仓',
    'after_market_close': '收盘',
}
POSITION_TYPE_ZH = {
    'PT_LONG_OSC': '震荡多',
    'PT_SHORT_OSC': '震荡空',
    'PT_LONG_RANGE_BREAK': '多头区间突破',
    'PT_SHORT_RANGE_BREAK': '空头区间突破',
}
REASON_ZH = {
    'osc_long_by_bottom_divergence': '震荡多-底背离开仓',
    'osc_short_by_top_divergence': '震荡空-顶背离开仓',
    '14:57_long_range_break': '14:57多头区间突破',
    'range_break_sell_confirmed': '区间突破做空确认',
    'range_break_sell_waiting': '区间突破做空等待',
    'long_osc_stop_loss': '震荡多止损',
    'short_osc_stop_loss': '震荡空止损',
    'long_osc_take_profit_by_top_divergence': '震荡多止盈-顶背离',
    'short_osc_take_profit_by_bottom_divergence': '震荡空止盈-底背离',
    'long_osc_take_profit_by_range_pct': '震荡多止盈-区间幅度',
    'short_osc_take_profit_by_range_pct': '震荡空止盈-区间幅度',
    'long_break_stop_loss': '多头突破止损',
    'short_break_stop_loss': '空头突破止损',
    'long_break_take_profit_by_top_divergence': '多头突破止盈-顶背离',
    'short_break_take_profit_bottom_divergence': '空头突破止盈-底背离',
    'short_break_take_profit_by_bottom_divergence': '空头突破止盈-底背离',
    'long_break_take_profit_5pct': '多头突破止盈-5%',
    'long_break_take_profit_1m_top_divergence': '多头突破止盈-1m顶背离',
    'short_break_take_profit_5pct': '空头突破止盈-5%',
    '移仓': '移仓',
    'roll_due': '到期移仓',
}
BRANCH_ZH = {
    'osc_long': '震荡多',
    'osc_short': '震荡空',
    '1457_long_break': '14:57多头突破',
    'range_break_confirm': '区间突破确认',
    'range_break_wait_start': '区间突破等待启动',
    'roll_close': '移仓平旧',
    'roll_open': '移仓开新',
}
RANGE_STATE_ZH = {True: '区间内', False: '区间外', 'inside': '区间内', 'breakout': '突破', 'unknown': '未知'}
BOOL_ZH = {True: '是', False: '否', None: '', '': ''}

EQUITY_HEADER_MAP = {
    'datetime': '日期时间',
    'cash': '现金',
    'available_cash': '可用资金',
    'margin_used': '占用保证金',
    'floating_pnl': '浮动盈亏',
    'total_value': '总权益',
    'mark_price': '收盘估价',
    'position_contract': '持仓合约',
    'position_amount': '持仓手数',
    'logical_anchor_entry': '锚定入场价',
    'roll_carry_points': '移仓累计盈亏',
    'drawdown': '相对初始回撤',
    'peak_drawdown': '峰值回撤',
}

STATE_HEADER_MAP = {
    'datetime': '日期时间',
    'event': '事件',
    'action': '动作',
    'reason': '原因',
    'position_side': '持仓方向',
    'position_contract': '持仓合约',
    'position_amount': '持仓手数',
    'position_type': '持仓类型',
    'entry_price': '入场价',
    'entry_time': '入场时间',
    'top_divergence_count': '顶背离计数',
    'bottom_divergence_count': '底背离计数',
    'day_lock': '当日锁',
    'switch_pending': '移仓待处理',
    'switch_stage': '移仓阶段',
    'switch_old': '旧合约',
    'switch_new': '新合约',
    'roll_carry_points': '移仓累计盈亏',
    'breakout_up_triggered': '向上突破已触发',
    'breakout_down_triggered': '向下突破已触发',
    'range_break_wait': '区间突破等待',
    'range_high': '区间上沿',
    'range_low': '区间下沿',
    'range_state': '价格相对区间',
    'extend_cnt': '中枢扩展次数',
}

NUMERIC_2DP_KEYS = frozenset({
    'price', 'signal_price', 'trade_price', 'pnl', 'total_pnl', 'commission',
    'available_cash', 'margin_used', 'total_value', 'cash', 'floating_pnl',
    'mark_price', 'logical_anchor_entry', 'roll_carry_points', 'entry_price',
    'range_high', 'range_low', 'old_price', 'new_price',
})


# format_number 方法说明
# 输入参数：value、nd（小数位）
# 输出参数：格式化数字或原值
# 用途：日志/CSV 数值展示
def format_number(value: Any, nd: int = 2) -> Any:
    if value is None or value == '':
        return value
    if isinstance(value, bool):
        return BOOL_ZH.get(value, value)
    try:
        return f'{float(value):.{nd}f}'
    except (TypeError, ValueError):
        return value


# format_ratio_percent 方法说明
# 输入参数：value（小数比例，如 0.0234）、nd（小数位）
# 输出参数：str（如 '2.34%'）或原值
# 用途：pnl_ratio、drawdown 等比例字段转百分数展示
def format_ratio_percent(value: Any, nd: int = 2) -> Any:
    if value is None or value == '':
        return value
    try:
        pct = float(value) * 100.0
        return f'{pct:.{nd}f}%'
    except (TypeError, ValueError):
        return value


# format_side 方法说明
# 输入参数：value（long/short 等）
# 输出参数：'多'/'空' 或原值
# 用途：持仓/成交方向中文化
def format_side(value: Any) -> Any:
    if value is None:
        return value
    return SIDE_ZH.get(str(value).lower(), value)


# format_action 方法说明
# 输入参数：value（open/close）
# 输出参数：'开'/'平' 或原值
# 用途：成交动作中文化
def format_action(value: Any) -> Any:
    if value is None:
        return value
    return ACTION_ZH.get(str(value).lower(), value)


# format_event 方法说明
# 输入参数：value（before_market_open/run_point 等）
# 输出参数：中文事件名或原值
# 用途：run_log/state 事件类型中文化
def format_event(value: Any) -> Any:
    if value is None:
        return value
    return EVENT_ZH.get(str(value), value)


# format_position_type 方法说明
# 输入参数：value（PT_LONG_OSC 等枚举）
# 输出参数：中文持仓类型或原值
# 用途：持仓类型列中文化
def format_position_type(value: Any) -> Any:
    if value is None:
        return value
    return POSITION_TYPE_ZH.get(str(value), value)


# format_reason 方法说明
# 输入参数：value（英文 reason 代码）
# 输出参数：中文原因说明或原值
# 用途：开平仓原因列中文化
def format_reason(value: Any) -> Any:
    if value is None:
        return value
    s = str(value)
    return REASON_ZH.get(s, s)


# format_range_state 方法说明
# 输入参数：value（True/False/inside/breakout 等）
# 输出参数：'区间内'/'区间外' 等或原值
# 用途：价格相对 range 状态中文化
def format_range_state(value: Any) -> Any:
    if value in RANGE_STATE_ZH:
        return RANGE_STATE_ZH[value]
    return value


# format_bool 方法说明
# 输入参数：value（bool）
# 输出参数：'是'/'否' 或原值
# 用途：布尔标志列中文化
def format_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return BOOL_ZH[value]
    return value


# format_trigger_detail_text 方法说明
# 输入参数：text（trigger_detail 英文详情串）
# 输出参数：str，替换方向/持仓类型/规则/分支等为中文
# 用途：trades.csv 触发条件列二次中文化
def format_trigger_detail_text(text: str) -> str:
    if not text:
        return text
    out = text
    for en, zh in SIDE_ZH.items():
        out = out.replace(f'方向={en}', f'方向={zh}')
        out = out.replace(f'，方向={en}，', f'，方向={zh}，')
    for code, zh in POSITION_TYPE_ZH.items():
        out = out.replace(f'持仓类型={code}', f'持仓类型={zh}')
    for code, zh in REASON_ZH.items():
        out = out.replace(f'规则={code}', f'规则={zh}')
        out = out.replace(f'分支={code}', f'分支={zh}')
    for code, zh in BRANCH_ZH.items():
        out = out.replace(f'分支={code}', f'分支={zh}')
    out = out.replace('价格相对区间=True', '价格相对区间=区间内')
    out = out.replace('价格相对区间=False', '价格相对区间=区间外')
    return out


# format_trade_row 方法说明
# 输入参数：row（成交记录 dict）
# 输出参数：dict（字段已中文化/格式化）
# 用途：trades.csv 行格式化
def format_trade_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out['action'] = format_action(out.get('action'))
    out['side'] = format_side(out.get('side'))
    out['position_type'] = format_position_type(out.get('position_type'))
    out['reason'] = format_reason(out.get('reason'))
    if out.get('trigger_detail'):
        out['trigger_detail'] = format_trigger_detail_text(str(out['trigger_detail']))
    if out.get('pnl_ratio') is not None and out.get('pnl_ratio') != '':
        out['pnl_ratio'] = format_ratio_percent(out['pnl_ratio'])
    for k in NUMERIC_2DP_KEYS:
        if k in out:
            out[k] = format_number(out.get(k))
    if out.get('wait_bars') is not None and out.get('wait_bars') != '':
        try:
            out['wait_bars'] = int(float(out['wait_bars']))
        except (TypeError, ValueError):
            pass
    for k, v in list(out.items()):
        if k.startswith('extra_'):
            out[k] = v
    return out


# format_equity_row 方法说明
# 输入参数：row（权益曲线 dict）
# 输出参数：dict（数值格式化、回撤转百分数、手数取整）
# 用途：equity.csv 行写出前格式化
def format_equity_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for k in NUMERIC_2DP_KEYS:
        if k in out:
            out[k] = format_number(out.get(k))
    for pct_key in ('drawdown', 'peak_drawdown'):
        if pct_key in out and out.get(pct_key) is not None and out.get(pct_key) != '':
            out[pct_key] = format_ratio_percent(out.get(pct_key))
    if out.get('position_amount') is not None and out.get('position_amount') != '':
        try:
            out['position_amount'] = int(float(str(out['position_amount']).replace(',', '')))
        except (TypeError, ValueError):
            pass
    return out


# format_state_row 方法说明
# 输入参数：row（状态快照 dict）
# 输出参数：dict（事件/方向/持仓类型/range/布尔/数值格式化）
# 用途：state_snapshots.csv 行写出前格式化
def format_state_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out['event'] = format_event(out.get('event'))
    raw_action = row.get('action')
    if raw_action in EVENT_ZH:
        out['action'] = format_event(raw_action)
    else:
        out['action'] = format_action(raw_action)
    out['position_side'] = format_side(out.get('position_side'))
    out['position_type'] = format_position_type(out.get('position_type'))
    out['range_state'] = format_range_state(out.get('range_state'))
    for k in ('switch_pending', 'breakout_up_triggered', 'breakout_down_triggered'):
        if k in out:
            out[k] = format_bool(out.get(k))
    for k in NUMERIC_2DP_KEYS:
        if k in out:
            out[k] = format_number(out.get(k))
    if out.get('roll_carry_points') is not None:
        out['roll_carry_points'] = format_number(out.get('roll_carry_points'))
    return out


# translate_headers 方法说明
# 输入参数：fieldnames（英文字段列表）、header_map（映射表）
# 输出参数：list[str]，中文表头
# 用途：CSV 写出时表头翻译
def translate_headers(fieldnames: list[str], header_map: dict[str, str]) -> list[str]:
    return [header_map.get(k, k) for k in fieldnames]
