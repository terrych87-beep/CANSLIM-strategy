"""新浪日 K（不复权原始价）。"""

from __future__ import annotations

import json
from typing import Optional

import pandas as pd

from screener.canslim import config
from screener.canslim.data_sina.http_util import (
    cache_path,
    http_get,
    read_json_cache,
    to_sina_symbol,
    write_json_cache,
)

_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20data=/"
    "CN_MarketData.getKLineData"
)


def fetch_daily_kline(
    symbol_or_code: str,
    *,
    datalen: int = config.KLINE_DATALEN,
    use_cache: bool = True,
) -> pd.DataFrame:
    """拉取日 K。返回列: date, open, high, low, close, volume；按日期升序。"""
    raw = str(symbol_or_code).strip().lower()
    symbol = raw if raw.startswith(("sh", "sz")) else to_sina_symbol(raw)

    cpath = cache_path("kline", f"{symbol}_{datalen}")
    if use_cache:
        cached = read_json_cache(cpath)
        if cached:
            df = pd.DataFrame(cached)
            df["date"] = pd.to_datetime(df["date"])
            return df.sort_values("date").reset_index(drop=True)

    url = f"{_KLINE_URL}?symbol={symbol}&scale=240&ma=no&datalen={int(datalen)}"
    resp = http_get(url, sleep=config.REQUEST_SLEEP_SEC)
    text = resp.text
    if "var data=(" not in text:
        raise RuntimeError(f"新浪日K格式异常 {symbol}: {text[:180]}")
    payload = text.split("var data=(", 1)[1].rsplit(");", 1)[0]
    rows = json.loads(payload)
    out = []
    for row in rows:
        day = pd.to_datetime(row.get("day"), errors="coerce")
        if pd.isna(day):
            continue
        out.append(
            {
                "date": day.normalize(),
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "volume": _f(row.get("volume")),
            }
        )
    df = pd.DataFrame(out).sort_values("date").reset_index(drop=True)
    if df.empty:
        raise RuntimeError(f"新浪日K为空: {symbol}")
    if use_cache:
        write_json_cache(
            cpath,
            [
                {
                    "date": r.date.strftime("%Y-%m-%d"),
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume,
                }
                for r in df.itertuples(index=False)
            ],
        )
    return df


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None
