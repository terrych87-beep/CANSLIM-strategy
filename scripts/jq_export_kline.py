#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：无（在聚宽回测环境中运行；修改下方 EXPORT_* 常量）
# 输出参数：回测日志中的 JQ_KLINE_* / BAR 行（供 parse_jq_kline_log.py 解析）
# 中文映射：
#   - EXPORT_FREQ -> 导出周期（1m/5m/30m/1d/1w）
#   - JQ_KLINE_ROW -> 日志中单行 CSV 数据标记
# 用途：从聚宽回测环境导出上证历史 K 线，补本地 1m 断档（尤其 14:57）
# 使用说明：
#   1. 复制到聚宽策略，回测 EXPORT_START ~ EXPORT_END
#   2. 保存日志 → parse_jq_kline_log.py → merge_jq_export_1m.py
# 注意：聚宽 Python 3.6，勿用 from __future__ import annotations

"""
聚宽【回测环境】脚本：导出上证指数历史 K 线（对齐本地 data/*.csv 列格式）

回测环境通常无法下载 CSV，因此默认把每行数据打印到日志（可解析前缀）。

使用步骤：
  1. 复制本文件到聚宽策略，回测日期 = EXPORT_START ~ EXPORT_END
  2. 复制回测日志全文保存为本地文件，例如 data/jq_backtest.log
  3. python scripts/parse_jq_kline_log.py --input data/jq_backtest.log --output data/jq_1m_sparse.csv
  4. python scripts/merge_jq_export_1m.py --input data/jq_1m_sparse.csv

日志标记（解析用，勿改）：
  JQ_KLINE_BEGIN|...
  JQ_KLINE_HEADER|<csv表头>
  JQ_KLINE_ROW|<csv数据行>
  JQ_KLINE_END|rows=...

注意：聚宽 Python 3.6，勿用 from __future__ import annotations。
"""

import csv
import pandas as pd
from io import StringIO

# ========================== 导出参数（按需修改） ==========================
INDEX = "000001.XSHG"
INDEX_CODE = "1"
INDEX_NAME = "上证指数"

EXPORT_START = "2023-10-13"
EXPORT_END = "2025-09-09"
EXPORT_FREQ = "1m"  # 1m / 5m / 30m / 1d / 1w

SPARSE_1M = True
SPARSE_MORNING_START = "09:30"
SPARSE_MORNING_END = "10:30"
SPARSE_AFTERNOON = "14:57"

# True=逐行打日志（推荐）；False 时尝试 write_file（回测环境通常不可用）
EXPORT_TO_LOG = True
EXPORT_TO_FILE = False
OUTPUT_CSV = "历史K线_000001_XSHG_1m_sparse_2023-10-13_to_2025-09-09.csv"

LOG_TAG_BEGIN = "JQ_KLINE_BEGIN"
LOG_TAG_HEADER = "JQ_KLINE_HEADER"
LOG_TAG_ROW = "JQ_KLINE_ROW"
LOG_TAG_END = "JQ_KLINE_END"

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


def _read_jq_file_text(path):
    try:
        raw = read_file(path)
    except Exception:
        return ""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8-sig")
    return str(raw)


def _write_jq_file_text(path, content):
    try:
        write_file(path, content)
    except TypeError:
        write_file(path, content.encode("utf-8-sig"))


def _csv_line(values):
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(values)
    return buf.getvalue().strip("\r\n")


def _cell_str(val):
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    if isinstance(val, pd.Timestamp):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    return str(val)


def _month_ranges(start, end):
    s = pd.to_datetime(start).to_period("M").to_timestamp()
    e = pd.to_datetime(end).to_period("M").to_timestamp()
    cur = s
    out = []
    while cur <= e:
        m_start = cur.strftime("%Y-%m-%d")
        m_end = (cur + pd.offsets.MonthEnd(1)).strftime("%Y-%m-%d")
        if pd.to_datetime(m_end) > pd.to_datetime(end):
            m_end = end
        out.append((m_start, m_end))
        cur = cur + pd.offsets.MonthBegin(1)
    return out


def _is_valid_1m_minute(ts):
    hm = ts.strftime("%H:%M")
    return ("09:31" <= hm <= "11:30") or ("13:01" <= hm <= "15:00")


def _filter_sparse_1m(df):
    if df is None or df.empty:
        return df
    out = df.copy()
    out["交易时间"] = pd.to_datetime(out["交易时间"])
    hm = out["交易时间"].dt.strftime("%H:%M")
    mask = (hm == SPARSE_AFTERNOON) | (
        (hm >= SPARSE_MORNING_START)
        & (hm <= SPARSE_MORNING_END)
        & out["交易时间"].map(_is_valid_1m_minute)
    )
    return out.loc[mask].reset_index(drop=True)


def _fetch_range(start, end, freq):
    df = get_price(
        INDEX,
        start_date=start,
        end_date=end,
        frequency=freq,
        fields=["open", "high", "low", "close", "volume", "money"],
        skip_paused=True,
        fq="pre",
        panel=False,
    )
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=CSV_COLUMNS)

    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    prev = df["close"].shift(1)
    chg = df["close"] - prev
    pct = (chg / prev) * 100.0

    out = pd.DataFrame(
        {
            "交易时间": df.index,
            "证券代码": INDEX_CODE,
            "证券名称": INDEX_NAME,
            "开盘价": df["open"],
            "最高价": df["high"],
            "最低价": df["low"],
            "收盘价": df["close"],
            "涨跌": chg.fillna(0.0),
            "涨跌幅%": pct.fillna(0.0),
            "成交量": df["volume"].fillna(0.0),
            "成交额": df["money"].fillna(0.0),
            "data_source": "jq_bt_%s" % freq,
        }
    )
    for col in CSV_COLUMNS:
        if col not in out.columns:
            out[col] = None
    out = out[CSV_COLUMNS]

    if freq == "1m" and SPARSE_1M:
        out = _filter_sparse_1m(out)
        if not out.empty:
            out = out[CSV_COLUMNS]
    return out


def _log_df_rows(df):
    if df is None or df.empty:
        return 0
    if not g.header_logged:
        log.info("%s|%s" % (LOG_TAG_HEADER, _csv_line(CSV_COLUMNS)))
        g.header_logged = True
    n = 0
    for _, row in df.iterrows():
        vals = [_cell_str(row[c]) for c in CSV_COLUMNS]
        log.info("%s|%s" % (LOG_TAG_ROW, _csv_line(vals)))
        n += 1
    return n


def _append_csv(path, df, append):
    if df is None or df.empty:
        return
    txt = df.to_csv(index=False, encoding="utf-8-sig", line_terminator="\n")
    if append:
        old = _read_jq_file_text(path)
        if old.strip():
            lines = txt.splitlines()
            body = "\n".join(lines[1:]) if len(lines) > 1 else ""
            if not body:
                return
            txt = old.rstrip("\n") + "\n" + body + "\n"
    _write_jq_file_text(path, txt)


def _bulk_export():
    freq = EXPORT_FREQ
    g.header_logged = False
    g.log_row_count = 0

    log.info(
        "%s|index=%s|start=%s|end=%s|freq=%s|sparse_1m=%s"
        % (LOG_TAG_BEGIN, INDEX, EXPORT_START, EXPORT_END, freq, SPARSE_1M)
    )
    log.info(
        "开始导出 %s %s~%s freq=%s sparse_1m=%s export_to_log=%s"
        % (INDEX, EXPORT_START, EXPORT_END, freq, SPARSE_1M, EXPORT_TO_LOG)
    )

    out_path = OUTPUT_CSV
    append = False
    total = 0
    file_total = 0

    if EXPORT_TO_FILE:
        _write_jq_file_text(out_path, "")

    if freq in ("1d", "1w"):
        df = _fetch_range(EXPORT_START, EXPORT_END, freq)
        if EXPORT_TO_LOG:
            g.log_row_count += _log_df_rows(df)
        if EXPORT_TO_FILE:
            _append_csv(out_path, df, append=False)
        total = len(df)
        log.info("导出完成 rows=%s log_rows=%s" % (total, g.log_row_count))
        log.info("%s|rows=%s" % (LOG_TAG_END, total))
        return

    chunks = _month_ranges(EXPORT_START, EXPORT_END)
    for i, (m_start, m_end) in enumerate(chunks):
        df = _fetch_range(m_start, m_end, freq)
        if df.empty:
            log.info("[%s] %s/%s %s~%s 无数据" % (freq, i + 1, len(chunks), m_start, m_end))
            continue
        if EXPORT_TO_LOG:
            logged = _log_df_rows(df)
            g.log_row_count += logged
        if EXPORT_TO_FILE:
            _append_csv(out_path, df, append=append)
            append = True
            file_total += len(df)
        total += len(df)
        log.info(
            "[%s] %s/%s %s~%s rows=%s total=%s log_total=%s"
            % (freq, i + 1, len(chunks), m_start, m_end, len(df), total, g.log_row_count)
        )

    log.info("导出完成 rows=%s log_rows=%s file=%s" % (total, g.log_row_count, out_path if EXPORT_TO_FILE else "N/A"))
    log.info("%s|rows=%s" % (LOG_TAG_END, total))


def initialize(context):
    set_benchmark(INDEX)
    g.export_done = False
    _bulk_export()
    g.export_done = True
    log.info("导出任务结束；请复制含 %s/%s 的日志到本地解析" % (LOG_TAG_HEADER, LOG_TAG_ROW))


def handle_data(context, data):
    pass


def after_trading_end(context):
    pass
