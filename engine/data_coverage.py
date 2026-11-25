# 模块说明
# 输入参数：DataPortal、start_date、end_date；可选 calendar_file（30m 交易日历 CSV）
# 输出参数：tuple[bool, str]（是否覆盖、说明文案）
# 中文映射：
#   - calendar_file -> 判定交易日与末 bar 的 CSV（默认 30m）
# 用途：run_backtest / live_data_sync 在 sync 前判断本地是否已满足区间
# 使用说明：结束日为非交易日时，按区间内最后可用交易日判断

from __future__ import annotations

import pandas as pd

from engine.data_portal import DataPortal

DEFAULT_TRADE_CALENDAR = '上证指数30分钟加入实时数据.csv'


# data_covers_backtest_range 方法说明
# 输入参数：data_portal、start_date、end_date；可选 calendar_file
# 输出参数：tuple[bool, str]（是否覆盖、说明文案）
# 用途：决定是否跳过 sync-data
def data_covers_backtest_range(
    data_portal: DataPortal,
    start_date,
    end_date,
    *,
    calendar_file: str = DEFAULT_TRADE_CALENDAR,
) -> tuple[bool, str]:
    start = pd.to_datetime(start_date).normalize()
    end = pd.to_datetime(end_date).normalize()
    if end < start:
        return False, f'结束日期 {end.date()} 早于起始日期 {start.date()}'

    try:
        trade_dates = data_portal.get_trade_dates(calendar_file)
    except Exception as e:
        return False, f'无法读取交易日历 {calendar_file}: {e}'

    if not trade_dates:
        return False, f'{calendar_file} 无交易日'

    in_range = [d for d in trade_dates if start <= pd.Timestamp(d).normalize() <= end]
    if not in_range:
        return False, f'区间内无交易日（数据末日本地 {pd.Timestamp(trade_dates[-1]).date()}）'

    first_needed = pd.Timestamp(in_range[0]).normalize()
    last_needed = pd.Timestamp(in_range[-1]).normalize()
    last_available = pd.Timestamp(trade_dates[-1]).normalize()
    if last_available < last_needed:
        return (
            False,
            f'数据不足：回测需要至 {last_needed.date()}，本地最后可用交易日 {last_available.date()}',
        )

    return (
        True,
        f'本地数据已覆盖 {first_needed.date()} ~ {last_needed.date()}（共 {len(in_range)} 个交易日）',
    )
