"""C / A 基本面因子。"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from screener.canslim import config


def score_c(fin: pd.DataFrame, *, thr: float = config.C_KF_YOY_MIN) -> dict[str, Any]:
    """扣非净利润同比（同报告期）。"""
    if fin is None or fin.empty:
        return _na("C", "无财务数据")
    latest = fin.iloc[0]
    period = str(latest.get("period") or "")
    if len(period) < 8:
        return _na("C", "报告期格式异常")
    yoy_period = _prev_year_period(period)
    cur = latest.get("扣非净利润")
    prev_row = fin.loc[fin["period"].astype(str) == yoy_period]
    if prev_row.empty:
        return _na("C", f"缺去年同期 {yoy_period}")
    prev = prev_row.iloc[0].get("扣非净利润")
    if cur is None or prev is None or pd.isna(cur) or pd.isna(prev) or float(prev) == 0:
        return _na("C", "扣非净利缺数或基期为0")
    yoy = float(cur) / float(prev) - 1.0
    passed = yoy >= thr
    return {
        "letter": "C",
        "value": yoy,
        "pass": passed,
        "detail": f"扣非同比={yoy:.1%} period={period} vs {yoy_period}",
        "period": period,
    }


def score_a(
    fin: pd.DataFrame,
    *,
    yoy_thr: float = config.A_NET_YOY_MIN,
    roe_thr: float = config.A_ROE_MIN,
) -> dict[str, Any]:
    """净利同比 + ROE。"""
    if fin is None or fin.empty:
        return _na("A", "无财务数据")
    latest = fin.iloc[0]
    period = str(latest.get("period") or "")
    roe = latest.get("ROE")
    growth = latest.get("归属母公司净利润增长率")

    # 增长率字段：样本中已是百分比数值（如 49.98）；若绝对值<=3 则可能是小数比率
    net_yoy = _normalize_growth(growth)
    if net_yoy is None:
        # 回退自算归母
        yoy_period = _prev_year_period(period)
        cur = latest.get("归母净利润")
        prev_row = fin.loc[fin["period"].astype(str) == yoy_period]
        if prev_row.empty or cur is None or pd.isna(cur):
            return _na("A", "净利同比缺数")
        prev = prev_row.iloc[0].get("归母净利润")
        if prev is None or pd.isna(prev) or float(prev) == 0:
            return _na("A", "归母基期缺数/为0")
        net_yoy = float(cur) / float(prev) - 1.0

    if roe is None or pd.isna(roe):
        return {
            "letter": "A",
            "value": None,
            "pass": None,
            "detail": f"ROE缺数; 净利同比={net_yoy:.1%}",
            "net_yoy": net_yoy,
            "roe": None,
            "period": period,
        }

    roe_f = float(roe)
    passed = (net_yoy >= yoy_thr) and (roe_f >= roe_thr)
    return {
        "letter": "A",
        "value": net_yoy,
        "pass": passed,
        "detail": f"净利同比={net_yoy:.1%} ROE={roe_f:.2f}% period={period}",
        "net_yoy": net_yoy,
        "roe": roe_f,
        "period": period,
    }


def _normalize_growth(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    x = float(v)
    # 接口多为百分比数值：49.98 → 0.4998；也兼容已经是比率的 -0.25
    if abs(x) > 3:
        return x / 100.0
    return x


def _prev_year_period(period: str) -> str:
    # YYYYMMDD
    y = int(period[:4]) - 1
    return f"{y}{period[4:]}"


def _na(letter: str, reason: str) -> dict[str, Any]:
    return {
        "letter": letter,
        "value": None,
        "pass": None,
        "detail": reason,
    }
