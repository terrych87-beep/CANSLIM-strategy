"""交易触发条件中文详情格式化。

输入参数：
  - action、reason、side、position_type、extra（阈值/价格/计数等）
输出参数：
  - str，多行中文触发说明（写入 trades.csv trigger_detail 列）
用途：
  - 回测成交记录可读化，说明开仓/平仓满足的具体条件

使用说明：
  - CenterMacdStrategy 撮合后调用 format_trigger_detail()
"""

from __future__ import annotations

from typing import Any, Optional

from engine.display_format import (
    BRANCH_ZH,
    REASON_ZH,
    format_position_type,
    format_range_state,
    format_ratio_percent,
    format_side,
)


def _f(v: Any, nd: int = 2) -> str:
    if v is None:
        return '无'
    try:
        return f'{float(v):.{nd}f}'
    except (TypeError, ValueError):
        return str(v)


def _b(v: Any) -> str:
    return '是' if v else '否'


# format_trigger_detail 方法说明
# 输入参数：action、reason；可选 side、position_type、extra
# 输出参数：str，格式化触发详情文本
# 用途：生成成交记录中的「触发条件」列
def format_trigger_detail(
    *,
    action: str,
    reason: str,
    side: Optional[str] = None,
    position_type: Optional[str] = None,
    extra: Optional[dict] = None,
) -> str:
    """将决策 extra 转为中文触发条件说明。"""
    ex = extra or {}
    parts: list[str] = []

    side_zh = format_side(side) if side else '未知'
    pos_zh = format_position_type(position_type) if position_type else '未知'

    if action == 'open':
        parts.append(f'动作=开，方向={side_zh}，持仓类型={pos_zh}')
        if ex.get('center_ready'):
            parts.append('中枢已就绪')
        if ex.get('range_high') is not None and ex.get('range_low') is not None:
            parts.append(f'区间上沿={_f(ex.get("range_high"))}，区间下沿={_f(ex.get("range_low"))}')
        if ex.get('range_pct') is not None:
            parts.append(f'区间幅度={format_ratio_percent(ex.get("range_pct"))}')
        if ex.get('range_state') is not None:
            parts.append(f'价格相对区间={format_range_state(ex.get("range_state"))}')
        parts.append(f'顶背离={_b(ex.get("has_top"))}，底背离={_b(ex.get("has_bottom"))}')
        parts.append(f'顶背离计数={ex.get("top_count", "无")}，底背离计数={ex.get("bottom_count", "无")}')
        wb = ex.get('wait_bars')
        if wb is not None:
            max_bars = ex.get('osc_wait_bars') or ex.get('break_wait_bars')
            if max_bars is not None:
                parts.append(f'等待bar数={wb}（上限{max_bars}）')
            else:
                parts.append(f'等待bar数={wb}')
        if ex.get('ref_rl') is not None:
            parts.append(f'突破参考下沿={_f(ex.get("ref_rl"))}')
        dl = ex.get('day_lock') or {}
        if dl:
            parts.append(f'当日禁多={_b(dl.get("ban_long"))}，当日禁空={_b(dl.get("ban_short"))}')
        if ex.get('cooldown_active'):
            parts.append('多头区间突破冷却中')
        branch = ex.get('branch') or reason
        parts.append(f'分支={BRANCH_ZH.get(str(branch), REASON_ZH.get(str(branch), str(branch)))}')
    elif action == 'close':
        parts.append(f'动作=平，方向={side_zh}，持仓类型={pos_zh}')
        if ex.get('logical_anchor_entry') is not None:
            parts.append(f'锚定价={_f(ex.get("logical_anchor_entry"))}')
        if ex.get('contract_price') is not None:
            parts.append(f'合约判断价={_f(ex.get("contract_price"))}')
        if ex.get('roll_carry_points') is not None and float(ex.get('roll_carry_points') or 0) != 0:
            parts.append(f'移仓累计盈亏={_f(ex.get("roll_carry_points"), 2)}')
        if ex.get('segment_pnl') is not None:
            parts.append(f'本段浮动盈亏={_f(ex.get("segment_pnl"), 2)}')
        if ex.get('total_pnl_amount') is not None:
            parts.append(f'两段合计盈亏={_f(ex.get("total_pnl_amount"), 2)}')
        if ex.get('pnl_ratio') is not None:
            parts.append(f'两段盈亏比例={format_ratio_percent(ex.get("pnl_ratio"))}')
        parts.append(f'顶背离={_b(ex.get("has_top"))}，底背离={_b(ex.get("has_bottom"))}')
        parts.append(f'顶背离计数={ex.get("top_count", "无")}，底背离计数={ex.get("bottom_count", "无")}')
        if ex.get('range_pct') is not None:
            parts.append(f'区间幅度={format_ratio_percent(ex.get("range_pct"))}')
        if 'top_count' in ex and reason.endswith('top_divergence'):
            parts.append(f'满足顶背离计数={ex.get("top_count")}')
        if 'bottom_count' in ex and reason.endswith('bottom_divergence'):
            parts.append(f'满足底背离计数={ex.get("bottom_count")}')
        dl = ex.get('day_lock') or {}
        if dl:
            parts.append(f'当日禁多={_b(dl.get("ban_long"))}，当日禁空={_b(dl.get("ban_short"))}')
        parts.append(f'规则={REASON_ZH.get(reason, reason)}')
    else:
        act = '开' if action == 'open' else ('平' if action == 'close' else action)
        parts.append(f'动作={act}，规则={REASON_ZH.get(reason, reason)}')

    return '；'.join(parts)
