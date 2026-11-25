"""EventEngine 事件调度测试。

输入参数：无
输出参数：pytest 断言
用途：验证交易日 RUNPOINTS 事件序列生成
"""

from __future__ import annotations

from datetime import date

from engine.event_engine import EventEngine


def test_event_count_and_order():
    engine = EventEngine()
    events = engine.build_daily_events(date(2020, 1, 2))

    assert len(events) == 11
    assert [e.time_str for e in events] == [
        '09:00', '10:00', '10:30', '11:00', '13:00', '13:30', '14:00', '14:30', '14:40', '14:57', '15:30'
    ]
    assert events[0].event_type == 'before_market_open'
    assert events[8].event_type == 'roll_contract'
    assert events[9].event_type == 'run_point_special'
    assert events[-1].event_type == 'after_market_close'
