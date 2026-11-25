"""S 量比 / L 相对强度（基于前复权价）。"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from screener.canslim import config


def score_s(
    kline_qfq: pd.DataFrame,
    *,
    short: int = config.VOL_SHORT,
    prior: int = config.VOL_LONG_PRIOR,
    thr: float = config.S_VOL_RATIO_MIN,
) -> dict[str, Any]:
    if kline_qfq is None or kline_qfq.empty or "volume" not in kline_qfq.columns:
        return _na("S", "无量能数据")
    vol = kline_qfq["volume"].astype(float)
    need = short + prior
    if len(vol) < need:
        return _na("S", f"K线不足 {len(vol)}<{need}")
    recent = vol.iloc[-short:]
    base = vol.iloc[-(need):-short]
    if base.mean() <= 0 or recent.isna().any() or base.isna().any():
        return _na("S", "均量缺数或基期为0")
    ratio = float(recent.mean() / base.mean())
    return {
        "letter": "S",
        "value": ratio,
        "pass": ratio >= thr,
        "detail": f"量比={ratio:.2f} (近{short}/前{prior})",
    }


def score_l(
    stock_qfq: pd.DataFrame,
    bench: pd.DataFrame,
    *,
    bars: int = config.LOOKBACK_6M_BARS,
    thr: float = config.L_EXCESS_6M_MIN,
) -> dict[str, Any]:
    """近约 6 个月相对沪深300超额收益。"""
    if stock_qfq is None or stock_qfq.empty or bench is None or bench.empty:
        return _na("L", "缺个股或基准K线")
    s = stock_qfq[["date", "close"]].dropna().copy()
    b = bench[["date", "close"]].dropna().copy()
    s["date"] = pd.to_datetime(s["date"]).dt.normalize()
    b["date"] = pd.to_datetime(b["date"]).dt.normalize()
    m = s.merge(b, on="date", suffixes=("_s", "_b")).sort_values("date")
    if len(m) < bars + 1:
        # 放宽：至少 100 根
        if len(m) < 100:
            return _na("L", f"对齐K线不足 {len(m)}")
        bars = len(m) - 1
    row0 = m.iloc[-(bars + 1)]
    row1 = m.iloc[-1]
    if row0["close_s"] <= 0 or row0["close_b"] <= 0:
        return _na("L", "基期收盘价无效")
    ret_s = float(row1["close_s"] / row0["close_s"] - 1.0)
    ret_b = float(row1["close_b"] / row0["close_b"] - 1.0)
    excess = ret_s - ret_b
    return {
        "letter": "L",
        "value": excess,
        "pass": excess >= thr,
        "detail": f"6M超额={excess:.1%} (股{ret_s:.1%} 基准{ret_b:.1%} bars={bars})",
        "ret_stock": ret_s,
        "ret_bench": ret_b,
    }


def near_52w_high(kline_qfq: pd.DataFrame) -> Optional[float]:
    """辅助：现价/近252日最高（前复权）。"""
    if kline_qfq is None or len(kline_qfq) < 60:
        return None
    closes = kline_qfq["close"].astype(float).dropna()
    window = closes.iloc[-min(252, len(closes)) :]
    hi = float(window.max())
    last = float(window.iloc[-1])
    if hi <= 0:
        return None
    return last / hi


def _na(letter: str, reason: str) -> dict[str, Any]:
    return {"letter": letter, "value": None, "pass": None, "detail": reason}
