#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：无
# 输出参数：stdout 打印各源末 24 根 30m 收盘价对比 JSON
# 用途：开发期单次重试探测，对比新浪/腾讯/东财/AkShare/baostock 末段数据一致性
# 性质：【开发探测】非回归必跑；依赖 probe_30m_sources.fetch_*
# 使用说明：python scripts/probe_30m_retry.py

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from probe_30m_sources import (  # noqa: E402
    BARS_NEED,
    fetch_akshare_em,
    fetch_baostock,
    fetch_eastmoney,
    fetch_sina,
    fetch_tencent,
)

UA = {"User-Agent": "Mozilla/5.0"}


def main():
    fns = [
        ("新浪", fetch_sina),
        ("东财 push2his", fetch_eastmoney),
        ("腾讯 ifzq", fetch_tencent),
        ("AkShare", fetch_akshare_em),
        ("Baostock", fetch_baostock),
    ]
    ok = []
    for name, fn in fns:
        try:
            df = fn()
            last = df.iloc[-1]
            print(f"[{name}] 通  rows={len(df)}  last={last['交易时间']}  close={last['收盘价']}")
            ok.append(name)
        except Exception as e:
            print(f"[{name}] 不通  {type(e).__name__}: {e}")

    print("\n通过:", ", ".join(ok) or "无")

    s = fetch_sina()
    t = fetch_tencent()
    s["交易时间"] = pd.to_datetime(s["交易时间"])
    t["交易时间"] = pd.to_datetime(t["交易时间"])
    m = s.merge(t, on="交易时间", suffixes=("_sina", "_tx")).tail(24)
    if not m.empty:
        diff = (m["收盘价_sina"] - m["收盘价_tx"]).abs().max()
        print(f"\n新浪 vs 腾讯 最近 {len(m)} 根最大收盘价差: {diff}")


if __name__ == "__main__":
    main()
