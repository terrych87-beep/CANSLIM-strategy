"""用分红事件对不复权日 K 做前复权（最新价不变）。"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def apply_qfq(
    kline: pd.DataFrame,
    bonus: pd.DataFrame,
    *,
    warnings: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    输入不复权 OHLC（列 date/open/high/low/close[/volume]）与分红事件。
    返回拷贝，OHLC 为前复权；volume 原样保留。
    算法见 docs/plans/CANSLIM_落地步骤.md §7.2.1。
    """
    out = kline.copy()
    if out.empty:
        return out
    out = out.sort_values("date").reset_index(drop=True)
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    # 因子必须用不复权昨收计算，不能读已经乘过的 out
    raw_close = out["close"].astype(float).copy()

    if bonus is None or bonus.empty:
        return out

    events = bonus.copy()
    events["ex_date"] = pd.to_datetime(events["ex_date"]).dt.normalize()
    events = events.sort_values("ex_date", ascending=False)  # 从新到旧

    warn = warnings if warnings is not None else []
    first_d = out["date"].iloc[0]
    last_d = out["date"].iloc[-1]

    for ev in events.itertuples(index=False):
        ex = pd.Timestamp(ev.ex_date).normalize()
        song = float(getattr(ev, "song_per_10", 0) or 0)
        zhuan = float(getattr(ev, "zhuan_per_10", 0) or 0)
        cash10 = float(getattr(ev, "cash_per_10", 0) or 0)
        stk = (song + zhuan) / 10.0
        cash = cash10 / 10.0
        if stk == 0 and cash == 0:
            continue

        # 截断窗口：仅处理落在 (first_date, last_date] 内的除权日，
        # 避免把「窗口前」事件乘到整段上（会破坏最新价不变）。
        if ex <= first_d or ex > last_d:
            continue

        pre_idx = out.index[out["date"] < ex]
        if len(pre_idx) == 0:
            warn.append(f"qfq skip {ex.date()}: no bar before ex_date")
            continue
        p_pre = float(raw_close.loc[pre_idx[-1]])
        if p_pre <= 0:
            warn.append(f"qfq skip {ex.date()}: P_pre<=0")
            continue
        denom = p_pre * (1.0 + stk)
        if denom <= 0:
            warn.append(f"qfq skip {ex.date()}: denom<=0")
            continue
        factor = (p_pre - cash) / denom
        if factor <= 0:
            warn.append(f"qfq skip {ex.date()}: factor={factor}")
            continue

        mask = out["date"] < ex
        for col in ("open", "high", "low", "close"):
            out.loc[mask, col] = out.loc[mask, col] * factor

    return out
