"""run_backtest.py CLI 子进程冒烟测试。

输入参数：无
输出参数：pytest 断言（退出码 0）
用途：验证命令行回测入口可执行
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_run_backtest_script_smoke():
    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, str(root / 'run_backtest.py'), '--start-date', '2021-01-01', '--end-date', '2021-01-08']
    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'events_per_day=11' in result.stdout
