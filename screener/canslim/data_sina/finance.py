"""新浪关键财务指标（gjzb）。"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from screener.canslim import config
from screener.canslim.data_sina.http_util import (
    cache_path,
    http_get,
    read_json_cache,
    to_sina_symbol,
    to_six_code,
    write_json_cache,
)

_FIN_URL = (
    "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
)


def fetch_finance_gjzb(code: str, *, num: int = 12, use_cache: bool = True) -> pd.DataFrame:
    """
    返回按报告期一行的宽表关键字段：
    period, 扣非净利润, 归母净利润, ROE, 归属母公司净利润增长率, 营业总收入增长率
    """
    six = to_six_code(code)
    paper = to_sina_symbol(six)
    cpath = cache_path("fin", f"{paper}_{num}")
    if use_cache:
        cached = read_json_cache(cpath)
        if cached:
            df = pd.DataFrame(cached)
            return df

    resp = http_get(
        _FIN_URL,
        params={
            "paperCode": paper,
            "source": "gjzb",
            "type": "0",
            "page": "1",
            "num": str(int(num)),
        },
        sleep=config.REQUEST_SLEEP_SEC,
    )
    payload = resp.json()
    data = ((payload.get("result") or {}).get("data")) or {}
    report_list = data.get("report_list") or {}
    date_meta = data.get("report_date") or []
    # 保持接口顺序（通常新→旧）
    periods = [d.get("date_value") for d in date_meta if d.get("date_value")]
    if not periods:
        periods = list(report_list.keys())

    rows = []
    for p in periods:
        items = (report_list.get(p) or {}).get("data") or []
        fmap = _items_to_map(items)
        rows.append(
            {
                "period": str(p),
                "扣非净利润": _pick(fmap, ["扣非净利润"]),
                "归母净利润": _pick(fmap, ["归母净利润"]),
                "ROE": _pick(fmap, ["净资产收益率(ROE)", "净资产收益率"]),
                "归属母公司净利润增长率": _pick(fmap, ["归属母公司净利润增长率"]),
                "营业总收入增长率": _pick(fmap, ["营业总收入增长率"]),
            }
        )
    df = pd.DataFrame(rows)
    if use_cache:
        write_json_cache(cpath, df.to_dict(orient="records"))
    return df


def _items_to_map(items: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for it in items:
        title = str(it.get("item_title") or "").strip()
        if not title or title in out:
            continue
        val = _to_float(it.get("item_value"))
        if val is not None:
            out[title] = val
    return out


def _pick(fmap: dict[str, float], names: list[str]) -> Optional[float]:
    for n in names:
        if n in fmap:
            return fmap[n]
    return None


def _to_float(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(str(v).replace(",", ""))
    except Exception:
        return None
