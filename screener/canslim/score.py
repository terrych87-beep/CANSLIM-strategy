"""单票 CANSLIM 打分。"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from screener.canslim import config
from screener.canslim.data_sina import (
    apply_qfq,
    fetch_bonus_events,
    fetch_daily_kline,
    fetch_finance_gjzb,
)
from screener.canslim.factors import score_a, score_c, score_l, score_m, score_s
from screener.canslim.factors.s_l import near_52w_high


def score_one(
    code: str,
    *,
    name: str = "",
    pool: str = "",
    bench_kline: Optional[pd.DataFrame] = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """对单只股票打分。缺数 pass=None，不计入通过也不算失败填0。"""
    warnings: list[str] = []
    result: dict[str, Any] = {
        "code": str(code).zfill(6) if str(code).isdigit() else str(code),
        "name": name,
        "pool": pool,
        "error": None,
    }
    try:
        fin = fetch_finance_gjzb(code, use_cache=use_cache)
        raw = fetch_daily_kline(code, use_cache=use_cache)
        bonus = fetch_bonus_events(code, use_cache=use_cache)
        qfq = apply_qfq(raw, bonus, warnings=warnings)

        if bench_kline is None:
            bench_kline = fetch_daily_kline(config.BENCHMARK_SYMBOL, use_cache=use_cache)

        c = score_c(fin)
        a = score_a(fin)
        s = score_s(qfq)
        l = score_l(qfq, bench_kline)
        m = score_m(bench_kline)

        letters = [c, a, s, l]
        passed = [x for x in letters if x.get("pass") is True]
        known = [x for x in letters if x.get("pass") is not None]
        x_of_4 = len(passed)
        known_n = len(known)

        result.update(
            {
                "C_pass": c.get("pass"),
                "C_value": c.get("value"),
                "C_detail": c.get("detail"),
                "A_pass": a.get("pass"),
                "A_net_yoy": a.get("net_yoy", a.get("value")),
                "A_roe": a.get("roe"),
                "A_detail": a.get("detail"),
                "S_pass": s.get("pass"),
                "S_value": s.get("value"),
                "S_detail": s.get("detail"),
                "L_pass": l.get("pass"),
                "L_value": l.get("value"),
                "L_detail": l.get("detail"),
                "M_gate": m.get("gate"),
                "M_detail": m.get("detail"),
                "X_of_4": x_of_4,
                "known_of_4": known_n,
                "near_52w_high": near_52w_high(qfq),
                "qfq_warnings": ";".join(warnings) if warnings else "",
                "last_close_qfq": float(qfq["close"].iloc[-1]) if not qfq.empty else None,
            }
        )
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["X_of_4"] = None
        result["known_of_4"] = None
        result["M_gate"] = "NA"
    return result
