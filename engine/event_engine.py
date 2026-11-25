from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, List


RUNPOINTS = [
    '09:00',
    '10:00',
    '10:30',
    '11:00',
    '13:00',
    '13:30',
    '14:00',
    '14:30',
    '14:40',
    '14:57',
    '15:30',
]


@dataclass(frozen=True)
class Event:
    # 数据结构说明
    # 输入参数：date（交易日期），time_str（时点字符串），event_type（事件类型）
    # 输出参数：Event 实例
    # 中文映射：
    #   - date -> 交易日期
    #   - time_str -> 运行时点
    #   - event_type -> 事件类型
    # 用途：描述策略在某个交易时点上的运行事件
    date: date
    time_str: str
    event_type: str

    @property
    def dt_str(self) -> str:
        return f"{self.date.isoformat()} {self.time_str}"


class EventEngine:
    # 模块说明
    # 输入参数：runpoints（运行时点列表，可选）
    # 输出参数：EventEngine 实例
    # 中文映射：
    #   - runpoints -> 运行时点列表
    #   - EventEngine -> 事件引擎
    # 用途：构建每日运行事件序列
    def __init__(self, runpoints: Iterable[str] | None = None):
        self.runpoints = list(runpoints) if runpoints is not None else list(RUNPOINTS)

    # build_daily_events 方法说明
    # 输入参数：trade_date（交易日期）
    # 输出参数：List[Event]（当日事件列表）
    # 中文映射：
    #   - trade_date -> 交易日期
    #   - runpoints -> 运行时点列表
    #   - Event -> 事件对象
    # 用途：为单个交易日生成按时点排列的事件序列
    def build_daily_events(self, trade_date) -> List[Event]:
        d = trade_date.date() if hasattr(trade_date, 'date') else trade_date
        return [Event(date=d, time_str=t, event_type=self._event_type(t)) for t in self.runpoints]

    # _event_type 方法说明
    # 输入参数：time_str（时间字符串）
    # 输出参数：str（事件类型）
    # 中文映射：
    #   - 09:00 -> before_market_open
    #   - 14:40 -> roll_contract
    #   - 15:30 -> after_market_close
    #   - 14:57 -> run_point_special
    #   - 其他 -> run_point
    # 用途：把时间点映射成策略事件类型
    @staticmethod
    def _event_type(time_str: str) -> str:
        if time_str == '09:00':
            return 'before_market_open'
        if time_str == '14:40':
            return 'roll_contract'
        if time_str == '15:30':
            return 'after_market_close'
        if time_str == '14:57':
            return 'run_point_special'
        return 'run_point'

    # build_events 方法说明
    # 输入参数：trade_dates（交易日期列表）
    # 输出参数：List[Event]（所有事件列表）
    # 中文映射：
    #   - trade_dates -> 交易日期列表
    #   - events -> 事件列表
    # 用途：批量生成多个交易日的事件序列
    def build_events(self, trade_dates: Iterable) -> List[Event]:
        events: List[Event] = []
        for d in trade_dates:
            events.extend(self.build_daily_events(d))
        return events
