"""M 大盘闸门（不进 X/4）。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from screener.canslim import config


def score_m(
    bench: pd.DataFrame,
    *,
    ma_fast: int = config.MA_FAST,
    ma_slow: int = config.MA_SLOW,
) -> dict[str, Any]:
    """
    简化闸门：收盘价相对 MA50/MA200。
    - 两者之上 → 进攻(green)
    - 仅上 MA50 → 中性(yellow)
    - 否则 → 防守(red)
    """
    if bench is None or bench.empty:
        return {
            "letter": "M",
            "value": None,
            "pass": None,
            "gate": "NA",
            "detail": "缺基准K线",
        }
    closes = bench["close"].astype(float).dropna()
    need = max(ma_fast, ma_slow)
    if len(closes) < need:
        return {
            "letter": "M",
            "value": None,
            "pass": None,
            "gate": "NA",
            "detail": f"基准K线不足 {len(closes)}<{need}",
        }
    last = float(closes.iloc[-1])
    ma50 = float(closes.iloc[-ma_fast:].mean())
    ma200 = float(closes.iloc[-ma_slow:].mean())
    above50 = last >= ma50
    above200 = last >= ma200
    if above50 and above200:
        gate = "green"
        detail = f"进攻: close={last:.2f} ≥ MA{ma_fast}/{ma_slow}"
        ok = True
    elif above50:
        gate = "yellow"
        detail = f"中性: close={last:.2f} ≥ MA{ma_fast} 但 < MA{ma_slow}"
        ok = True
    else:
        gate = "red"
        detail = f"防守: close={last:.2f} < MA{ma_fast} (MA{ma_slow}={ma200:.2f})"
        ok = False
    return {
        "letter": "M",
        "value": last,
        "pass": ok,
        "gate": gate,
        "detail": detail,
        "ma50": ma50,
        "ma200": ma200,
    }
