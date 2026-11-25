"""DataLoader CSV 加载与列标准化测试。

输入参数：无（读取 data/ 样例文件）
输出参数：pytest 断言
用途：验证行情 CSV 加载与 datetime/OHLC 列映射
"""

from __future__ import annotations

from pathlib import Path

from engine.data_loader import DataLoader
from engine.data_portal import DataPortal


def test_data_files_readable():
    root = Path(__file__).resolve().parents[1]
    data_dir = root / 'data'
    loader = DataLoader(data_dir)

    day_file = '上证指数日线加入实时数据.csv'
    m30_file = '上证指数30分钟加入实时数据.csv'
    if_file = 'IF_front_next_runpoints.csv'

    day_df = loader.load_csv(day_file)
    m30_df = loader.load_csv(m30_file)
    if_df = loader.load_csv(if_file)

    assert not day_df.empty
    assert not m30_df.empty
    assert not if_df.empty
    assert 'datetime' in day_df.columns
    assert 'close' in m30_df.columns
    assert 'front_contract' in if_df.columns


def test_asof_query_and_if_price():
    root = Path(__file__).resolve().parents[1]
    portal = DataPortal(root / 'data')

    bars = portal.get_index_bars('上证指数30分钟加入实时数据.csv', '2020-01-02 13:00:00', count=260)
    assert bars is not None
    assert 'datetime' in bars.columns

    front, nxt = portal.get_if_contracts('2020-01-02 14:40:00')
    assert front is not None or nxt is not None
