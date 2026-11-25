from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd

from .data_loader import DataLoader

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
if str(_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_DATA_DIR))
import bar_filters as _bf

_DAILY_FILENAME = "上证指数日线加入实时数据.csv"


class DataPortal:
    # 模块说明
    # 输入参数：base_dir（数据目录，可选）
    # 输出参数：DataPortal 实例
    # 中文映射：
    #   - base_dir -> 数据目录
    #   - DataPortal -> 本地行情门户
    # 用途：统一读取本项目 data/ 下的行情数据
    """本地行情门户：只读取本项目 data/ 下的数据。"""

    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parents[1] / 'data'
        self.loader = DataLoader(self.base_dir)
        self._cache: dict[str, pd.DataFrame] = {}

    # load_frame 方法说明
    # 输入参数：filename（文件名）
    # 输出参数：pd.DataFrame（缓存后的数据表）
    # 中文映射：
    #   - filename -> 文件名
    #   - _cache -> 缓存字典
    #   - df -> 数据表
    # 用途：读取并缓存单个数据文件
    def load_frame(self, filename: str) -> pd.DataFrame:
        if filename not in self._cache:
            self._cache[filename] = self.loader.load_csv(filename)
        return self._cache[filename]

    def _filter_daily_for_asof(self, df: pd.DataFrame, as_of) -> pd.DataFrame:
        if df is None or df.empty or "datetime" not in df.columns:
            return df
        return _bf.drop_future_and_unclosed(
            df,
            pd.Timestamp(as_of),
            "1d",
            time_col="datetime",
            calendar_path=self.base_dir / _DAILY_FILENAME,
        )

    # get_trade_dates 方法说明
    # 输入参数：filename（交易日数据文件名）
    # 输出参数：list[pd.Timestamp]（交易日期列表）
    # 中文映射：
    #   - filename -> 交易日数据文件名
    #   - trade_dates -> 交易日期
    # 用途：读取交易日列表
    def get_trade_dates(self, filename: str = '上证指数日线加入实时数据.csv') -> list[pd.Timestamp]:
        df = self.load_frame(filename)
        if df.empty:
            return []
        date_col = 'datetime' if 'datetime' in df.columns else ('交易时间' if '交易时间' in df.columns else None)
        if date_col is None:
            return []
        dates = pd.to_datetime(df[date_col], errors='coerce').dropna()
        return [pd.Timestamp(x).normalize() for x in dates.dt.normalize().drop_duplicates().tolist()]

    # is_trade_date 方法说明
    # 输入参数：current_dt（当前日期时间），filename（交易日数据文件名）
    # 输出参数：bool（是否为交易日）
    # 中文映射：
    #   - current_dt -> 当前日期时间
    #   - trade_dates -> 交易日期列表
    #   - ts -> 当前日期
    # 用途：判断当前日期是否属于交易日
    def is_trade_date(self, current_dt, filename: str = '上证指数日线加入实时数据.csv') -> bool:
        ts = pd.to_datetime(current_dt).normalize()
        trade_dates = set(self.get_trade_dates(filename))
        return ts in trade_dates

    # _asof_slice 方法说明
    # 输入参数：df（数据表），end_dt（截止时间），count（条数）
    # 输出参数：pd.DataFrame（按截止时间截取的数据）
    # 中文映射：
    #   - end_dt -> 截止时间
    #   - count -> 条数
    #   - work -> 过滤后的数据
    # 用途：按截止时间做窗口切片
    @staticmethod
    def _asof_slice(df: pd.DataFrame, end_dt, count: int) -> pd.DataFrame:
        if df is None or df.empty or 'datetime' not in df.columns:
            return pd.DataFrame()
        ts = pd.to_datetime(end_dt)
        work = df[df['datetime'] <= ts].copy()
        if work.empty:
            return pd.DataFrame()
        return work.tail(int(count)).reset_index(drop=True)

    # get_index_bars 方法说明
    # 输入参数：filename（文件名），end_dt（截止时间），count（数量）
    # 输出参数：pd.DataFrame（指数K线）
    # 中文映射：
    #   - filename -> 文件名
    #   - end_dt -> 截止时间
    #   - count -> 条数
    # 用途：获取指定截止时间前的指数K线
    def get_index_bars(self, filename: str, end_dt, count: int = 260) -> pd.DataFrame:
        df = self.load_frame(filename)
        if filename == _DAILY_FILENAME:
            df = self._filter_daily_for_asof(df, end_dt)
        return self._asof_slice(df, end_dt, count)

    # get_index_latest_price 方法说明
    # 输入参数：filename（文件名），end_dt（截止时间）
    # 输出参数：float | None（最新收盘价）
    # 中文映射：
    #   - filename -> 文件名
    #   - end_dt -> 截止时间
    #   - close -> 收盘价
    # 用途：获取截止时间前最近一根指数收盘价
    def get_index_latest_price(self, filename: str, end_dt) -> Optional[float]:
        bars = self.get_index_bars(filename, end_dt, count=1)
        if bars.empty or 'close' not in bars.columns:
            return None
        return float(bars['close'].iloc[-1])

    def get_index_1m_close_at_1457(self, filename: str, as_of) -> Optional[float]:
        """仅当 as_of 所在交易日存在 14:57 这根 1m bar 时返回收盘价，否则 None（禁止 asof 回退）。"""
        df = self.load_frame(filename)
        if df.empty or 'close' not in df.columns or 'datetime' not in df.columns:
            return None
        day = pd.to_datetime(as_of).normalize()
        target = day + pd.Timedelta(hours=14, minutes=57)
        dts = pd.to_datetime(df['datetime'], errors='coerce')
        hit = df.loc[dts == target, 'close']
        if hit.empty:
            return None
        val = float(hit.iloc[-1])
        if pd.isna(val):
            return None
        return val

    # get_index_daily_until_prev_trade_day 方法说明
    # 输入参数：filename（文件名），current_dt（当前时间），count（数量）
    # 输出参数：pd.DataFrame（前一交易日之前的日线）
    # 中文映射：
    #   - current_dt -> 当前时间
    #   - prev -> 前一交易日前数据
    # 用途：获取前一交易日前的日线窗口
    def get_index_daily_until_prev_trade_day(self, filename: str, current_dt, count: int = 260) -> pd.DataFrame:
        df = self._filter_daily_for_asof(self.load_frame(filename), current_dt)
        if df.empty:
            return pd.DataFrame()
        ts = pd.to_datetime(current_dt)
        day = ts.normalize()
        prev = df[df['datetime'] < day].copy()
        if prev.empty:
            return pd.DataFrame()
        return prev.tail(int(count)).reset_index(drop=True)

    # load_if_runpoints 方法说明
    # 输入参数：filename（IF运行点文件名）
    # 输出参数：pd.DataFrame（运行点数据表）
    # 中文映射：
    #   - filename -> IF运行点文件名
    #   - df -> 运行点数据表
    # 用途：读取IF前后月运行点数据
    def load_if_runpoints(self, filename: str = 'IF_front_next_runpoints.csv') -> pd.DataFrame:
        df = self.load_frame(filename)
        if 'datetime' in df.columns:
            df = df.sort_values('datetime').drop_duplicates(subset=['datetime'], keep='last').reset_index(drop=True)
        return df

    # get_if_row 方法说明
    # 输入参数：current_dt（当前时间）
    # 输出参数：pd.Series | None（对应运行点记录）
    # 中文映射：
    #   - current_dt -> 当前时间
    #   - row -> 行记录
    # 用途：获取当前时点可用的最新IF运行点记录
    def get_if_row(self, current_dt) -> Optional[pd.Series]:
        df = self.load_if_runpoints()
        if df.empty:
            return None
        ts = pd.to_datetime(current_dt)
        work = df[df['datetime'] <= ts].copy()
        if work.empty:
            return None
        return work.iloc[-1]

    # get_if_price 方法说明
    # 输入参数：current_dt（当前时间），contract（合约代码）
    # 输出参数：float | None（合约价格）
    # 中文映射：
    #   - contract -> 合约代码
    #   - front_contract -> 主力合约
    #   - next_contract -> 次主力合约
    #   - front_close -> 主力收盘价
    #   - next_close -> 次主力收盘价
    def get_if_price(self, current_dt, contract: str) -> Optional[float]:
        row = self.get_if_row(current_dt)
        if row is None:
            return None
        contract = str(contract)
        front = str(row.get('front_contract') or '')
        nxt = str(row.get('next_contract') or '')
        if contract == front:
            return float(row.get('front_close')) if pd.notna(row.get('front_close')) else None
        if contract == nxt:
            return float(row.get('next_close')) if pd.notna(row.get('next_close')) else None
        return None

    # get_if_price_with_fallback 方法说明
    # 输入参数：current_dt（当前时间），contract（合约代码），fallback_dt（兜底时间）
    # 输出参数：tuple[float | None, str]（价格与取值模式）
    # 中文映射：
    #   - fallback_dt -> 兜底时间
    #   - price -> 价格
    #   - price_mode -> 价格模式
    # 用途：优先按当前时间取价，失败时回退到兜底时间
    def get_if_price_with_fallback(self, current_dt, contract: str, fallback_dt=None) -> tuple[Optional[float], str]:
        price = self.get_if_price(current_dt, contract)
        if price is not None:
            return price, 'exact'
        if fallback_dt is not None:
            price = self.get_if_price(fallback_dt, contract)
            if price is not None:
                return price, 'fallback_14:30'
        row = self.get_if_row(current_dt)
        if row is None:
            return None, 'missing'
        front = str(row.get('front_contract') or '')
        nxt = str(row.get('next_contract') or '')
        if contract == front and pd.notna(row.get('front_close')):
            return float(row.get('front_close')), 'asof_front'
        if contract == nxt and pd.notna(row.get('next_close')):
            return float(row.get('next_close')), 'asof_next'
        return None, 'missing'

    # get_if_contracts 方法说明
    # 输入参数：current_dt（当前时间）
    # 输出参数：tuple[Optional[str], Optional[str]]（主力与次主力合约）
    # 中文映射：
    #   - front_contract -> 主力合约
    #   - next_contract -> 次主力合约
    # 用途：读取当前时点主力/次主力合约
    def get_if_contracts(self, current_dt) -> tuple[Optional[str], Optional[str]]:
        row = self.get_if_row(current_dt)
        if row is None:
            return None, None
        return str(row.get('front_contract')), str(row.get('next_contract'))
