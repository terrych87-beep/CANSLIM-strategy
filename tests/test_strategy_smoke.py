"""策略模块冒烟测试（DataPortal + 策略初始化）。

输入参数：无
输出参数：pytest 断言
用途：验证策略依赖的数据门户与基本构造不报错
"""

from __future__ import annotations

from pathlib import Path

from engine.data_portal import DataPortal
from engine.event_engine import EventEngine
from strategy.center_macd_strategy import CenterMacdStrategy


def test_strategy_bootstrap_and_events():
    root = Path(__file__).resolve().parents[1]
    portal = DataPortal(root / 'data')
    strategy = CenterMacdStrategy(portal)
    strategy.initialize()

    engine = EventEngine()
    events = engine.build_daily_events('2020-01-02')

    r0 = strategy.before_market_open(events[0])
    r1 = strategy.handle_run_point(events[1])
    r2 = strategy.after_market_close(events[-1])

    assert r0['action'] == 'before_market_open'
    assert r1['action'] == 'run_point'
    assert r2['action'] == 'after_market_close'
    assert len(strategy.logs) == 3
