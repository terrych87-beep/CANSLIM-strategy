#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：CLI --input（聚宽日志路径），--output（输出 CSV，可选）
# 输出参数：本地标准列 CSV（与 data/上证指数*.csv 列格式一致）
# 中文映射：
#   - JQ_KLINE_ROW / BAR -> 日志行解析标记
#   - 交易时间 -> datetime 列
# 用途：将 jq_export_kline.py 或聚宽回测日志还原为可合并的 CSV
# 使用说明：
#   python scripts/parse_jq_kline_log.py --input data/jq_backtest.log --output data/jq_1m_sparse.csv

import argparse
import csv
import re
from pathlib import Path

import pandas as pd

TAG_HEADER = "JQ_KLINE_HEADER|"
TAG_ROW = "JQ_KLINE_ROW|"
TAG_END = "JQ_KLINE_END|"

BAR_RE = re.compile(
    r"BAR unit=(?P<unit>\S+) now=(?P<now>[\d\- :]+) bar_dt=\s*"
    r"O=(?P<o>[-\d.]+) H=(?P<h>[-\d.]+) L=(?P<l>[-\d.]+) C=(?P<c>[-\d.]+) "
    r"V=(?P<v>[-\d.]+) M=(?P<m>[-\d.]+)"
)

CSV_COLUMNS = [
    "交易时间",
    "证券代码",
    "证券名称",
    "开盘价",
    "最高价",
    "最低价",
    "收盘价",
    "涨跌",
    "涨跌幅%",
    "成交量",
    "成交额",
    "data_source",
    "新浪开盘价",
    "新浪最高价",
    "新浪最低价",
    "新浪收盘价",
    "新浪涨跌",
    "新浪涨跌幅%",
    "新浪成交量",
    "新浪成交额",
    "收盘价差异相对比率",
]


def _extract_payload(line, tag):
    idx = line.find(tag)
    if idx < 0:
        return None
    return line[idx + len(tag) :].strip()


def _parse_end_rows(raw):
    for tag in ("JQ_EXPORT_END|", TAG_END):
        if tag in raw:
            part = raw.split(tag, 1)[-1].strip()
            if part.startswith("rows="):
                try:
                    return int(part.split("=", 1)[1])
                except ValueError:
                    pass
    return None


def parse_log(text):
    bar_rows = []
    legacy_header = None
    legacy_rows = []
    end_rows = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        er = _parse_end_rows(line)
        if er is not None:
            end_rows = er
        if TAG_HEADER in line:
            payload = _extract_payload(line, TAG_HEADER)
            if payload:
                legacy_header = next(csv.reader([payload]))
        elif TAG_ROW in line:
            payload = _extract_payload(line, TAG_ROW)
            if payload:
                legacy_rows.append(next(csv.reader([payload])))
        m = BAR_RE.search(line)
        if m:
            g = m.groupdict()
            bar_rows.append(
                {
                    "交易时间": pd.to_datetime(g["now"].strip()),
                    "unit": g["unit"],
                    "开盘价": float(g["o"]),
                    "最高价": float(g["h"]),
                    "最低价": float(g["l"]),
                    "收盘价": float(g["c"]),
                    "成交量": float(g["v"]),
                    "成交额": float(g["m"]),
                }
            )
    return bar_rows, legacy_header, legacy_rows, end_rows


def to_local_csv(rows):
    if not rows:
        return pd.DataFrame(columns=CSV_COLUMNS)
    df = pd.DataFrame(rows).sort_values("交易时间").reset_index(drop=True)
    prev = df["收盘价"].shift(1)
    chg = df["收盘价"] - prev
    pct = (chg / prev * 100.0).fillna(0.0)
    unit = df["unit"].iloc[0] if "unit" in df.columns and len(df) else "1m"
    out = pd.DataFrame(
        {
            "交易时间": df["交易时间"].dt.strftime("%Y-%m-%d %H:%M:%S"),
            "证券代码": "1",
            "证券名称": "上证指数",
            "开盘价": df["开盘价"],
            "最高价": df["最高价"],
            "最低价": df["最低价"],
            "收盘价": df["收盘价"],
            "涨跌": chg.fillna(0.0),
            "涨跌幅%": pct,
            "成交量": df["成交量"],
            "成交额": df["成交额"],
            "data_source": "jq_bt_%s" % unit,
        }
    )
    for col in CSV_COLUMNS:
        if col not in out.columns:
            out[col] = None
    return out[CSV_COLUMNS]


def legacy_to_dataframe(header, rows):
    if not header or not rows:
        return pd.DataFrame(columns=CSV_COLUMNS)
    out = pd.DataFrame(rows, columns=header[: len(rows[0])])
    for col in CSV_COLUMNS:
        if col not in out.columns:
            out[col] = None
    return out[CSV_COLUMNS]


def main():
    parser = argparse.ArgumentParser(description="解析聚宽 BAR / JQ_KLINE 日志为 CSV")
    parser.add_argument("--input", required=True, help="回测日志 txt")
    parser.add_argument("--output", required=True, help="输出 CSV 路径")
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8", errors="ignore")
    bar_rows, legacy_header, legacy_rows, end_rows = parse_log(text)
    if legacy_rows:
        out_df = legacy_to_dataframe(legacy_header, legacy_rows)
        fmt = "JQ_KLINE_ROW"
    elif bar_rows:
        out_df = to_local_csv(bar_rows)
        fmt = "BAR"
    else:
        raise SystemExit("未找到 BAR 或 JQ_KLINE_ROW 数据行")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out, index=False, encoding="utf-8-sig")

    print("[完成] format=%s rows=%s end_tag_rows=%s -> %s" % (fmt, len(out_df), end_rows, out))
    if end_rows is not None and end_rows != len(out_df):
        print("[警告] END rows=%s 与解析行数 %s 不一致" % (end_rows, len(out_df)))


if __name__ == "__main__":
    main()
