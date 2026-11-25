#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：无（内置模拟时刻 2026-06-24 10:58 / 11:26 等）
# 输出参数：stdout 打印 OK / assert 失败；exit 0 表示规则通过
# 中文映射：
#   - latest_closed_bar_time -> 已收盘 bar 边界
#   - drop_future_and_unclosed -> 过滤后 DataFrame
# 用途：离线自检 v3.1 未收盘 K 线规则，不依赖网络；纳入 BASELINE_v3.1 回归清单
# 使用说明：python scripts/verify_unclosed_bar_filter.py

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))

import bar_filters as bf  # noqa: E402


def _trade_dates_june_2026() -> list[pd.Timestamp]:
    return [
        pd.Timestamp("2026-06-22"),
        pd.Timestamp("2026-06-23"),
        pd.Timestamp("2026-06-24"),
    ]


def test_30m_at_1126_max_is_1100() -> None:
    now = pd.Timestamp("2026-06-24 11:26:00")
    limit = bf.latest_closed_bar_time(now, "30m")
    assert limit == pd.Timestamp("2026-06-24 11:00:00"), limit
    assert limit != pd.Timestamp("2026-06-24 11:30:00")

    raw = pd.DataFrame(
        {
            "交易时间": [
                "2026-06-24 10:30:00",
                "2026-06-24 11:00:00",
                "2026-06-24 11:30:00",
            ],
            "close": [1.0, 2.0, 3.0],
        }
    )
    out = bf.drop_future_and_unclosed(raw, now, "30m", time_col="交易时间")
    assert out["交易时间"].max() == pd.Timestamp("2026-06-24 11:00:00")
    assert len(out) == 2


def test_5m_at_1058_no_1100() -> None:
    now = pd.Timestamp("2026-06-24 10:58:00")
    limit = bf.latest_closed_bar_time(now, "5m")
    assert limit == pd.Timestamp("2026-06-24 10:55:00"), limit


def test_daily_before_1500_no_today() -> None:
    now = pd.Timestamp("2026-06-24 11:26:00")
    dates = _trade_dates_june_2026()
    limit = bf.latest_closed_daily_date(now, dates)
    assert limit == pd.Timestamp("2026-06-23"), limit

    raw = pd.DataFrame(
        {
            "交易时间": ["2026-06-23", "2026-06-24"],
            "close": [3000.0, 3010.0],
        }
    )
    raw["交易时间"] = pd.to_datetime(raw["交易时间"])
    out = bf.drop_future_and_unclosed(
        raw, now, "1d", time_col="交易时间", trade_dates=dates
    )
    assert out["交易时间"].max().normalize() == pd.Timestamp("2026-06-23")
    assert len(out) == 1


def test_daily_after_1500_allows_today_on_trade_day() -> None:
    now = pd.Timestamp("2026-06-24 15:05:00")
    dates = _trade_dates_june_2026()
    limit = bf.latest_closed_daily_date(now, dates)
    assert limit == pd.Timestamp("2026-06-24"), limit


def test_daily_after_1500_allows_today_not_yet_in_calendar() -> None:
    """CSV 历尚无 today 时，收盘后仍应允许当日（30m 聚合日线写盘）。"""
    now = pd.Timestamp("2026-06-24 15:05:00")
    dates = [pd.Timestamp("2026-06-22"), pd.Timestamp("2026-06-23")]
    limit = bf.latest_closed_daily_date(now, dates)
    assert limit == pd.Timestamp("2026-06-24"), limit
    raw = pd.DataFrame(
        {
            "交易时间": ["2026-06-23", "2026-06-24"],
            "close": [3000.0, 3010.0],
        }
    )
    raw["交易时间"] = pd.to_datetime(raw["交易时间"])
    out = bf.drop_future_and_unclosed(
        raw, now, "1d", time_col="交易时间", trade_dates=dates
    )
    assert out["交易时间"].max().normalize() == pd.Timestamp("2026-06-24")
    assert len(out) == 2


def main() -> None:
    tests = [
        test_30m_at_1126_max_is_1100,
        test_5m_at_1058_no_1100,
        test_daily_before_1500_no_today,
        test_daily_after_1500_allows_today_on_trade_day,
        test_daily_after_1500_allows_today_not_yet_in_calendar,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
    print("all passed")


if __name__ == "__main__":
    main()
