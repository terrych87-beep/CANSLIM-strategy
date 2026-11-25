"""IncrementalMacdSession 与全量 check 结果一致性。

输入参数：无（使用 data/ 下 30m CSV）
输出参数：pytest 断言通过/失败
用途：验证增量 MACD 与全量重算背离结果一致
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pandas as pd

from engine.data_portal import DataPortal
from strategy.macd_divergence import IncrementalMacdSession, MacdDivCfg, macd_from_close, DivergenceDetector


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'data'

RUN_POINTS = ['10:00', '10:30', '11:00', '13:00', '13:30', '14:00', '14:30', '14:57']


@pytest.mark.skipif(not DATA_DIR.exists(), reason='缺少 data 目录')
def test_incremental_macd_matches_full_recompute():
    portal = DataPortal(str(DATA_DIR))
    cfg = MacdDivCfg()
    session = IncrementalMacdSession(cfg, portal)
    session.begin_day('2021-06-15')
    detector = DivergenceDetector(cfg)

    for time_str in RUN_POINTS:
        as_of = f'2021-06-15 {time_str}:00'
        inc = session.check(as_of)
        assert inc is not None, as_of

        df = portal.get_index_bars('上证指数30分钟加入实时数据.csv', as_of, count=260)
        close = df['close'].astype(float)
        close.index = pd.to_datetime(df['datetime'])
        full_div = detector.detect(close, macd_from_close(close, cfg))

        assert bool(inc['divergence']['has_top']) == bool(full_div['has_top']), time_str
        assert bool(inc['divergence']['has_bottom']) == bool(full_div['has_bottom']), time_str
        assert float(inc['current_price']) == float(close.iloc[-1]), time_str
