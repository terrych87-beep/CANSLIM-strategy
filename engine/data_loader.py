from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable

import pandas as pd


STANDARD_COL_MAP = {
    '交易时间': 'datetime',
    '开盘价': 'open',
    '最高价': 'high',
    '最低价': 'low',
    '收盘价': 'close',
    '成交量': 'volume',
    '成交额': 'money',
    '涨跌': 'change',
    '涨跌幅%': 'pct_change',
    '证券代码': 'symbol',
    '证券名称': 'name',
}


class DataLoader:
    # 模块说明
    # 输入参数：base_dir（数据目录）
    # 输出参数：DataLoader 实例
    # 中文映射：
    #   - base_dir -> 数据目录
    #   - DataLoader -> 本地数据读取与标准化模块
    # 用途：负责从本地数据目录加载并标准化数据
    """本地数据读取与标准化。"""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)

    # load_csv 方法说明
    # 输入参数：filename（文件名），parse_datetime（是否解析时间）
    # 输出参数：pd.DataFrame（标准化后的数据表）
    # 中文映射：
    #   - filename -> 文件名
    #   - parse_datetime -> 是否解析时间列
    #   - datetime -> 时间列
    #   - open/high/low/close -> OHLC价格列
    #   - volume -> 成交量
    #   - money -> 成交额
    # 用途：加载单个CSV并进行标准化
    def load_csv(self, filename: str, *, parse_datetime: bool = True) -> pd.DataFrame:
        path = self.base_dir / filename
        if not path.exists():
            raise FileNotFoundError(f'数据文件不存在: {path}')

        df = pd.read_csv(path, encoding='utf-8-sig')
        return self.normalize_frame(df, parse_datetime=parse_datetime)

    # normalize_frame 方法说明
    # 输入参数：df（原始数据表），parse_datetime（是否解析时间）
    # 输出参数：pd.DataFrame（标准化后的数据表）
    # 中文映射：
    #   - df -> 原始数据表
    #   - datetime -> 标准时间列
    #   - numeric_cols -> 数值列集合
    # 用途：统一列名、清洗时间列与数值列
    @staticmethod
    def normalize_frame(df: pd.DataFrame, *, parse_datetime: bool = True) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()

        out = df.copy()
        out = out.rename(columns={k: v for k, v in STANDARD_COL_MAP.items() if k in out.columns})

        if parse_datetime and 'datetime' in out.columns:
            out['datetime'] = pd.to_datetime(out['datetime'], errors='coerce')
            out = out.dropna(subset=['datetime'])

        numeric_cols = [c for c in ('open', 'high', 'low', 'close', 'volume', 'money', 'change', 'pct_change') if c in out.columns]
        for col in numeric_cols:
            out[col] = pd.to_numeric(out[col], errors='coerce')

        if 'datetime' in out.columns:
            out = out.sort_values('datetime').drop_duplicates(subset=['datetime'], keep='last').reset_index(drop=True)
        else:
            out = out.reset_index(drop=True)

        return out

    # load_many 方法说明
    # 输入参数：filenames（文件名列表）
    # 输出参数：Dict[str, pd.DataFrame]（文件名到数据表映射）
    # 中文映射：
    #   - filenames -> 文件名列表
    #   - result -> 加载结果字典
    # 用途：批量加载多个数据文件
    def load_many(self, filenames: Iterable[str]) -> Dict[str, pd.DataFrame]:
        result: Dict[str, pd.DataFrame] = {}
        for name in filenames:
            result[name] = self.load_csv(name)
        return result
