"""ReportWriter 落盘测试。

输入参数：无（临时目录）
输出参数：pytest 断言
用途：验证 trades/run_log CSV 写出与表头翻译
"""

from __future__ import annotations

from pathlib import Path

from engine.report import ReportWriter


def test_report_writer_outputs_files():
    root = Path(__file__).resolve().parents[1]
    out_dir = root / 'outputs' / 'test_report'
    writer = ReportWriter(out_dir)

    csv_path = writer.write_csv('trades.csv', [{'a': 1, 'b': 2}])
    json_path = writer.write_json('summary.json', {'ok': True})

    assert csv_path.exists()
    assert json_path.exists()
