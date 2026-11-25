#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：无（拉取最近约 3 个交易日 30m）
# 输出参数：stdout JSON/表格：各公开源（新浪、东财、腾讯、AkShare、baostock）是否可用
# 中文映射：
#   - BARS_NEED -> 期望最少 bar 数（约 3 日 × 8 根/日）
#   - 交易时间 -> 各源返回的时间列
# 用途：开发期对比多数据源 30m 可用性与延迟
# 性质：【开发探测】非回归必跑；供 probe_30m_retry.py 复用 fetch_* 函数
# 使用说明：python scripts/probe_30m_sources.py

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from typing import Any, Callable

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
# 3 交易日 × 8 根/日 ≈ 24，留余量
BARS_NEED = 40


def _ok(name: str, df: pd.DataFrame, note: str = "") -> dict[str, Any]:
    if df is None or df.empty:
        return {"source": name, "ok": False, "rows": 0, "error": "empty", "note": note}
    t = pd.to_datetime(df["交易时间"], errors="coerce").dropna()
    return {
        "source": name,
        "ok": True,
        "rows": len(df),
        "first": str(t.min()),
        "last": str(t.max()),
        "tail_close": float(df.iloc[-1]["收盘价"]) if "收盘价" in df.columns else None,
        "note": note,
    }


def _fail(name: str, err: str) -> dict[str, Any]:
    return {"source": name, "ok": False, "rows": 0, "error": err[:300]}


def fetch_sina() -> pd.DataFrame:
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20data=/CN_MarketData.getKLineData"
        f"?symbol=sh000001&scale=30&ma=no&datalen={BARS_NEED}"
    )
    r = requests.get(url, timeout=20, headers=UA)
    r.raise_for_status()
    text = r.text
    if "var data=(" not in text:
        raise RuntimeError(f"格式异常: {text[:120]}")
    payload = text.split("var data=(", 1)[1].rsplit(");", 1)[0]
    rows = json.loads(payload)
    data = []
    for row in rows:
        data.append(
            {
                "交易时间": pd.to_datetime(row["day"]),
                "开盘价": float(row["open"]),
                "最高价": float(row["high"]),
                "最低价": float(row["low"]),
                "收盘价": float(row["close"]),
                "成交量": row.get("volume"),
            }
        )
    return pd.DataFrame(data).sort_values("交易时间").reset_index(drop=True)


def fetch_eastmoney() -> pd.DataFrame:
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": "1.000001",
        "klt": "30",
        "fqt": "0",
        "lmt": str(BARS_NEED),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    r = requests.get(url, params=params, timeout=20, headers=UA)
    r.raise_for_status()
    j = r.json()
    if j.get("rc") != 0 or not j.get("data") or not j["data"].get("klines"):
        raise RuntimeError(f"rc={j.get('rc')} msg={j.get('message') or j}")
    data = []
    for line in j["data"]["klines"]:
        parts = line.split(",")
        # f51=时间 f52开 f53收 f54高 f55低 f56量 f57额
        data.append(
            {
                "交易时间": pd.to_datetime(parts[0]),
                "开盘价": float(parts[1]),
                "收盘价": float(parts[2]),
                "最高价": float(parts[3]),
                "最低价": float(parts[4]),
                "成交量": float(parts[5]) if len(parts) > 5 else None,
            }
        )
    return pd.DataFrame(data).sort_values("交易时间").reset_index(drop=True)


def fetch_tencent() -> pd.DataFrame:
    # web.ifzq.gtimg.cn 常 302 到 web3 且 SSL 失败；ifzq.gtimg.cn 直连可用
    url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
    params = {"param": f"sh000001,m30,,{BARS_NEED}"}
    r = requests.get(url, params=params, timeout=20, headers=UA)
    r.raise_for_status()
    j = json.loads(r.text)
    node = j.get("data", {}).get("sh000001", {})
    rows = node.get("m30") or []
    if not rows:
        raise RuntimeError(f"无 m30 字段 keys={list(node.keys())}")
    data = []
    for row in rows:
        # [time YYYYMMDDHHMM, open, close, high, low, volume, ...]
        ts = pd.to_datetime(str(row[0]), format="%Y%m%d%H%M", errors="coerce")
        data.append(
            {
                "交易时间": ts,
                "开盘价": float(row[1]),
                "收盘价": float(row[2]),
                "最高价": float(row[3]),
                "最低价": float(row[4]),
                "成交量": float(row[5]) if len(row) > 5 else None,
            }
        )
    return pd.DataFrame(data).sort_values("交易时间").reset_index(drop=True)


def fetch_akshare_em() -> pd.DataFrame:
    import akshare as ak
    from datetime import timedelta

    end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    df = ak.index_zh_a_hist_min_em(
        symbol="000001", period="30", start_date=start, end_date=end
    )
    if df is None or df.empty:
        raise RuntimeError("akshare 返回空")
    df = df.tail(BARS_NEED).copy()
    col_map = {
        "时间": "交易时间",
        "开盘": "开盘价",
        "收盘": "收盘价",
        "最高": "最高价",
        "最低": "最低价",
        "成交量": "成交量",
    }
    out = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    keep = [c for c in ["交易时间", "开盘价", "最高价", "最低价", "收盘价", "成交量"] if c in out.columns]
    return out[keep].sort_values("交易时间").reset_index(drop=True)


def fetch_baostock() -> pd.DataFrame:
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"login: {lg.error_msg}")
    try:
        end = datetime.now().strftime("%Y-%m-%d")
        rs = bs.query_history_k_data_plus(
            "sh.000001",
            "date,time,open,high,low,close,volume",
            start_date="2025-01-01",
            end_date=end,
            frequency="30",
            adjustflag="3",
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            raise RuntimeError(rs.error_msg or "无数据")
        df = pd.DataFrame(rows, columns=rs.fields)
        df["交易时间"] = pd.to_datetime(df["date"] + " " + df["time"].str.zfill(6), errors="coerce")
        for c, t in [("开盘价", "open"), ("最高价", "high"), ("最低价", "low"), ("收盘价", "close"), ("成交量", "volume")]:
            df[c] = pd.to_numeric(df[t], errors="coerce")
        return df.tail(BARS_NEED)[["交易时间", "开盘价", "最高价", "最低价", "收盘价", "成交量"]].reset_index(drop=True)
    finally:
        bs.logout()


def _run(name: str, fn: Callable[[], pd.DataFrame]) -> dict[str, Any]:
    try:
        df = fn()
        return _ok(name, df)
    except ImportError as e:
        return _fail(name, f"未安装依赖: {e}")
    except Exception as e:
        return _fail(name, f"{e}\n{traceback.format_exc(limit=2)}")


def main() -> int:
    probes: list[tuple[str, Callable[[], pd.DataFrame]]] = [
        ("新浪 quotes.sina.cn", fetch_sina),
        ("东方财富 push2his", fetch_eastmoney),
        ("腾讯 web.ifzq.gtimg.cn", fetch_tencent),
        ("AkShare→东财 index_zh_a_hist_min_em", fetch_akshare_em),
        ("Baostock 30m", fetch_baostock),
    ]

    print(f"探测时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"目标: 上证指数 sh000001 / 30分钟 / 约最近 {BARS_NEED} 根（≈3 交易日）\n")
    print(f"{'数据源':<36} {'状态':<6} {'条数':<6} {'末根时间':<22} {'末收':<10} 备注")
    print("-" * 100)

    results = []
    for name, fn in probes:
        r = _run(name, fn)
        results.append(r)
        if r["ok"]:
            print(
                f"{r['source']:<36} {'通':<6} {r['rows']:<6} {r['last']:<22} "
                f"{r.get('tail_close', ''):<10.2f} {r.get('note', '')}"
            )
        else:
            err = r.get("error", "").replace("\n", " | ")
            print(f"{r['source']:<36} {'不通':<6} {'-':<6} {'-':<22} {'-':<10} {err[:55]}")

    ok_names = [r["source"] for r in results if r["ok"]]
    print("\n汇总: 通", len(ok_names), "/", len(results), "→", ", ".join(ok_names) if ok_names else "无")
    return 0 if ok_names else 1


if __name__ == "__main__":
    sys.exit(main())
