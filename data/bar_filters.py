#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：各函数见下方（df、now_dt、freq、time_col、calendar_path 等）
# 输出参数：过滤后的 DataFrame、已收盘 bar 边界时刻、是否需剔除当日日线等
# 中文映射：
#   - now_dt -> 当前时刻（判定边界用）
#   - freq -> 周期（1m/5m/30m/1d/1w）
#   - 交易时间 / time_col -> K 线标签时间列
#   - latest_closed_bar_time -> 当前已收盘的最后一根 bar 标签时间
#   - drop_future_and_unclosed -> 剔除未来与未收盘 K 线
# 用途：实时同步、IF 运行点、data_portal 共用的未收盘/未来 bar 过滤（v3.1 数据口径）
# 使用说明：由 data/实时数据运行.py、data/期货合约数据获取.py、engine/data_portal.py 导入

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

DAILY_CLOSE_HOUR = 15
DEFAULT_TRADE_CALENDAR = Path(__file__).resolve().parent / "上证指数日线加入实时数据.csv"

_TRADE_DATES_CACHE: dict[str, list[pd.Timestamp]] = {}


def is_after_daily_close(now_dt: pd.Timestamp) -> bool:
    now_dt = pd.Timestamp(now_dt)
    return now_dt >= now_dt.normalize() + pd.Timedelta(hours=DAILY_CLOSE_HOUR)


def load_trade_dates(calendar_path: Path | None = None) -> list[pd.Timestamp]:
    path = Path(calendar_path) if calendar_path is not None else DEFAULT_TRADE_CALENDAR
    key = str(path.resolve())
    if key in _TRADE_DATES_CACHE:
        return _TRADE_DATES_CACHE[key]
    if not path.is_file():
        _TRADE_DATES_CACHE[key] = []
        return []
    df = pd.read_csv(path, encoding="utf-8-sig", usecols=["交易时间"])
    dates = pd.to_datetime(df["交易时间"], errors="coerce").dt.normalize().dropna().unique()
    out = sorted(pd.Timestamp(d) for d in dates)
    _TRADE_DATES_CACHE[key] = out
    return out


def prev_trade_day(
    now_dt: pd.Timestamp,
    trade_dates: list[pd.Timestamp] | None = None,
    *,
    calendar_path: Path | None = None,
) -> pd.Timestamp:
    now_dt = pd.Timestamp(now_dt)
    today = now_dt.normalize()
    dates = trade_dates if trade_dates is not None else load_trade_dates(calendar_path)
    past = [d for d in dates if d < today]
    if past:
        return max(past)
    return today - pd.Timedelta(days=1)


def latest_closed_daily_date(
    now_dt: pd.Timestamp,
    trade_dates: list[pd.Timestamp] | None = None,
    *,
    calendar_path: Path | None = None,
) -> pd.Timestamp:
    # 方法说明
    # 输入参数：now_dt，trade_dates/calendar_path
    # 输出参数：pd.Timestamp（日线允许的最大标签日期；15:00 前为上一交易日）
    now_dt = pd.Timestamp(now_dt)
    today = now_dt.normalize()
    dates = trade_dates if trade_dates is not None else load_trade_dates(calendar_path)
    if not is_after_daily_close(now_dt):
        return prev_trade_day(now_dt, dates, calendar_path=calendar_path)
    # 收盘后：允许当日标签（新交易日可能尚未写入 CSV 历，否则 30m 聚合日线会在写前被滤掉）
    if today in dates:
        return today
    if not dates or today > max(dates):
        return today
    past = [d for d in dates if d <= today]
    if past:
        return max(past)
    return prev_trade_day(now_dt, dates, calendar_path=calendar_path)


def is_valid_minute_time(dt: pd.Timestamp, freq: str) -> bool:
    hm = pd.Timestamp(dt).strftime("%H:%M")
    if freq == "1m":
        return ("09:31" <= hm <= "11:30") or ("13:01" <= hm <= "15:00")
    if freq == "5m":
        return (("09:35" <= hm <= "11:30") or ("13:05" <= hm <= "15:00")) and dt.minute % 5 == 0
    if freq == "30m":
        return hm in {"10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"}
    return True


def session_bar_end_times(freq: str, day: pd.Timestamp) -> list[pd.Timestamp]:
    day = pd.Timestamp(day).normalize()
    if freq == "30m":
        hms = ("10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00")
        return [day + pd.Timedelta(hours=int(h), minutes=int(m)) for h, m in (x.split(":") for x in hms)]
    if freq == "5m":
        out: list[pd.Timestamp] = []
        for h in range(9, 16):
            for m in range(0, 60, 5):
                ts = day + pd.Timedelta(hours=h, minutes=m)
                if is_valid_minute_time(ts, "5m"):
                    out.append(ts)
        return out
    if freq == "1m":
        out: list[pd.Timestamp] = []
        for h in range(9, 16):
            for m in range(0, 60):
                ts = day + pd.Timedelta(hours=h, minutes=m)
                if is_valid_minute_time(ts, "1m"):
                    out.append(ts)
        return out
    return []


def latest_closed_bar_time(
    now_dt: pd.Timestamp,
    freq: str,
    trade_dates: list[pd.Timestamp] | None = None,
    *,
    calendar_path: Path | None = None,
) -> pd.Timestamp:
    # 方法说明
    # 输入参数：now_dt（当前时刻），freq（周期），trade_dates/calendar_path（交易日历，可选）
    # 输出参数：pd.Timestamp（该周期下已收盘的最后一根 bar 标签时间）
    # 用途：例 11:26 时 30m 返回 11:00；10:58 时 5m 返回 10:55
    now_dt = pd.Timestamp(now_dt)
    day = now_dt.normalize()
    if freq == "1d":
        return latest_closed_daily_date(now_dt, trade_dates, calendar_path=calendar_path)
    if freq == "1w":
        return day
    if freq == "1m":
        if now_dt.strftime("%H:%M") < "09:31":
            return day + pd.Timedelta(hours=9, minutes=30)
        if "11:30" < now_dt.strftime("%H:%M") < "13:01":
            return day + pd.Timedelta(hours=11, minutes=30)
        if now_dt.strftime("%H:%M") >= "15:00":
            return day + pd.Timedelta(hours=15, minutes=0)
        closed = now_dt.floor("min") - pd.Timedelta(minutes=1)
        if closed.strftime("%H:%M") == "13:00":
            closed = day + pd.Timedelta(hours=11, minutes=30)
        return max(closed, day + pd.Timedelta(hours=9, minutes=30))
    ends = session_bar_end_times(freq, day)
    passed = [t for t in ends if t <= now_dt]
    if not passed:
        return day + pd.Timedelta(hours=9, minutes=30)
    return max(passed)


def drop_future_and_unclosed(
    df: pd.DataFrame,
    now_dt: pd.Timestamp,
    freq: str,
    *,
    time_col: str = "交易时间",
    trade_dates: list[pd.Timestamp] | None = None,
    calendar_path: Path | None = None,
) -> pd.DataFrame:
    # 方法说明
    # 输入参数：df（K 线表），now_dt，freq，time_col（时间列名），calendar_path（日线历，1d 用）
    # 输出参数：pd.DataFrame（仅保留 bar 结束时刻 <= now 的行；1d 在 15:00 前不含当日）
    # 用途：写入 CSV 前、data_portal 查询前的统一过滤入口
    if df is None or df.empty:
        return df.iloc[0:0].copy() if df is not None and time_col in df.columns else pd.DataFrame()
    d = df.copy()
    d[time_col] = pd.to_datetime(d[time_col], errors="coerce")
    d = d.dropna(subset=[time_col])
    now_dt = pd.Timestamp(now_dt)
    if freq in {"1d", "1w"}:
        limit = latest_closed_bar_time(now_dt, freq, trade_dates, calendar_path=calendar_path)
        d = d[d[time_col].dt.normalize() <= pd.Timestamp(limit).normalize()]
    else:
        limit = latest_closed_bar_time(now_dt, freq, trade_dates, calendar_path=calendar_path)
        d = d[d[time_col] <= limit]
    return d.sort_values(time_col).reset_index(drop=True)


def needs_strip_today_daily(
    df: pd.DataFrame,
    now_dt: pd.Timestamp,
    *,
    time_col: str = "交易时间",
) -> bool:
    # 方法说明
    # 输入参数：df（日线表），now_dt，time_col
    # 输出参数：bool（15:00 前且 df 含当日行时为 True，触发写前剔除）
    if is_after_daily_close(now_dt):
        return False
    if df is None or df.empty or time_col not in df.columns:
        return False
    today = pd.Timestamp(now_dt).normalize()
    times = pd.to_datetime(df[time_col], errors="coerce").dt.normalize()
    return bool((times == today).any())
