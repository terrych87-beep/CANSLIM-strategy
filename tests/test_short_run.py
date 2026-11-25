"""短区间数据切片冒烟测试。

输入参数：无
输出参数：pytest 断言
用途：验证 DataPortal get_index_bars 等基本取数
"""

from __future__ import annotations

from pathlib import Path

from engine.data_portal import DataPortal
from engine.event_engine import EventEngine
from engine.report import ReportWriter
from strategy.center_macd_strategy import CenterMacdStrategy


def test_short_run_smoke():
    project_root = Path(__file__).resolve().parents[1]
    data_dir = project_root / 'data'
    out_dir = project_root / 'outputs' / 'test_short_run'
    portal = DataPortal(data_dir)
    strategy = CenterMacdStrategy(portal)
    strategy.initialize()
    engine = EventEngine()
    report = ReportWriter(out_dir)

    assert len(engine.runpoints) == 11
    report.write_json('summary.json', {'ok': True, 'events_per_day': len(engine.runpoints)})
    assert (out_dir / 'summary.json').exists()
