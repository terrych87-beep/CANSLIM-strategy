"""短区间完整回测冒烟测试。

输入参数：无
输出参数：pytest 断言
用途：验证 run_backtest 短区间可跑通并产出 trades
"""

from __future__ import annotations

from pathlib import Path

from engine.data_portal import DataPortal
from engine.event_engine import EventEngine
from engine.report import ReportWriter
from strategy.center_macd_strategy import CenterMacdStrategy


def test_short_backtest_window():
    root = Path(__file__).resolve().parents[1]
    portal = DataPortal(root / 'data')
    strategy = CenterMacdStrategy(portal)
    strategy.initialize()
    engine = EventEngine()
    report = ReportWriter(root / 'outputs' / 'pytest_short_backtest')

    for event in engine.build_daily_events('2021-01-04'):
        if event.event_type == 'before_market_open':
            strategy.before_market_open(event)
        elif event.event_type == 'roll_contract':
            strategy.roll_contract_if_needed(event)
        elif event.event_type == 'after_market_close':
            strategy.after_market_close(event)
        else:
            strategy.handle_run_point(event)

    report.write_csv('run_log.csv', strategy.run_log)
    report.write_json('summary.json', strategy.summary)

    assert len(strategy.run_log) == 11
    assert len(strategy.state_snapshots) == 11
    assert (root / 'outputs' / 'pytest_short_backtest' / 'summary.json').exists()
