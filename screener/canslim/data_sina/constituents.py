"""沪深300 / 中证1000 成分。"""

from __future__ import annotations

import json
import re
from typing import Optional

import pandas as pd

from screener.canslim import config
from screener.canslim.data_sina.http_util import (
    cache_path,
    http_get,
    read_json_cache,
    write_json_cache,
)

_COUNT_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeStockCountSimple"
)
_NODE_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_HTML_URL = (
    "https://vip.stock.finance.sina.com.cn/corp/view/vII_NewestComponent.php"
)


def fetch_hs300(*, use_cache: bool = True) -> pd.DataFrame:
    """沪深300：JSON 分页 node=hs300。列 code, symbol, name, pool。"""
    cpath = cache_path("cons", "hs300")
    if use_cache:
        cached = read_json_cache(cpath)
        if cached:
            return pd.DataFrame(cached)

    count_resp = http_get(_COUNT_URL, params={"node": "hs300"})
    count = int(re.sub(r"[^\d]", "", count_resp.text) or "0")
    pages = max(1, (count + 79) // 80)
    rows: list[dict] = []
    for page in range(1, pages + 1):
        resp = http_get(
            _NODE_URL,
            params={
                "page": str(page),
                "num": "80",
                "sort": "symbol",
                "asc": "1",
                "node": "hs300",
                "symbol": "",
                "_s_r_a": "init",
            },
            sleep=config.REQUEST_SLEEP_SEC,
        )
        batch = json.loads(resp.text)
        for item in batch:
            code = str(item.get("code") or "").zfill(6)
            symbol = str(item.get("symbol") or "")
            name = item.get("name") or ""
            rows.append(
                {"code": code, "symbol": symbol, "name": name, "pool": "HS300"}
            )
    df = pd.DataFrame(rows).drop_duplicates(subset=["code"]).reset_index(drop=True)
    if use_cache:
        write_json_cache(cpath, df.to_dict(orient="records"))
    return df


def fetch_csi1000(*, use_cache: bool = True) -> pd.DataFrame:
    """中证1000：HTML 翻页 indexid=000852。"""
    cpath = cache_path("cons", "csi1000")
    if use_cache:
        cached = read_json_cache(cpath)
        if cached:
            return pd.DataFrame(cached)

    # 先读第 1 页拿总页数
    first = http_get(
        _HTML_URL,
        params={"page": "1", "indexid": config.CSI1000_INDEX_ID},
        sleep=config.REQUEST_SLEEP_SEC,
    )
    page_count = _parse_page_count(first.text, default=25)
    rows: list[dict] = []
    for page in range(1, page_count + 1):
        if page == 1:
            html = first.text
        else:
            resp = http_get(
                _HTML_URL,
                params={"page": str(page), "indexid": config.CSI1000_INDEX_ID},
                sleep=config.REQUEST_SLEEP_SEC,
            )
            html = resp.text
        rows.extend(_parse_html_cons_page(html, pool="CSI1000"))

    df = pd.DataFrame(rows).drop_duplicates(subset=["code"]).reset_index(drop=True)
    if use_cache:
        write_json_cache(cpath, df.to_dict(orient="records"))
    return df


def fetch_pool_hs300_csi1000(*, use_cache: bool = True) -> pd.DataFrame:
    """并集去重；若同时在两池，pool 标 HS300+CSI1000。"""
    a = fetch_hs300(use_cache=use_cache)
    b = fetch_csi1000(use_cache=use_cache)
    merged = pd.concat([a, b], ignore_index=True)
    if merged.empty:
        return merged

    def _join_pool(s: pd.Series) -> str:
        parts = sorted(set(s.dropna().astype(str)))
        return "+".join(parts)

    g = (
        merged.groupby("code", as_index=False)
        .agg({"symbol": "first", "name": "first", "pool": _join_pool})
        .sort_values("code")
        .reset_index(drop=True)
    )
    # 补 symbol
    mask = g["symbol"].isna() | (g["symbol"].astype(str) == "")
    g.loc[mask, "symbol"] = g.loc[mask, "code"].map(_guess_symbol)
    return g


def _guess_symbol(code: str) -> str:
    from screener.canslim.data_sina.http_util import to_sina_symbol

    try:
        return to_sina_symbol(code)
    except Exception:
        return str(code)


def _parse_page_count(html: str, default: int = 25) -> int:
    m = re.search(r'goto_page\(document\.getElementById\("page_num"\)\.value,(\d+)\)', html)
    if m:
        return int(m.group(1))
    m = re.search(r"page=(\d+)&indexid=\d{6}\">尾页", html)
    if m:
        return int(m.group(1))
    return default


def _parse_html_cons_page(html: str, pool: str) -> list[dict]:
    # <div align="center">300277</div> ... <a ...>名称</a>
    pat = re.compile(
        r'<div align="center">(\d{6})</div>\s*</td>\s*'
        r'<td><div align="center"><a[^>]*>([^<]+)</a></div>',
        re.IGNORECASE,
    )
    rows = []
    for m in pat.finditer(html):
        code = m.group(1)
        name = m.group(2).strip()
        rows.append(
            {
                "code": code,
                "symbol": _guess_symbol(code),
                "name": name,
                "pool": pool,
            }
        )
    return rows
