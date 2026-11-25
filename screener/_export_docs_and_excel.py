# -*- coding: utf-8 -*-
"""将 CANSLIM 说明 Markdown 导出为 PDF，并由全池 CSV 生成明细 Excel。"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows

ROOT = Path(__file__).resolve().parents[1]
SCREENER = Path(__file__).resolve().parent
OUT_DIR = SCREENER
CSV_FULL = ROOT / "outputs" / "canslim_screen_full.csv"
FONT = r"C:\Windows\Fonts\msyh.ttc"

MD_FILES = [
    (SCREENER / "CANSLIM_使用说明.md", OUT_DIR / "CANSLIM_使用说明.pdf"),
    (SCREENER / "CANSLIM_模块需求说明书.md", OUT_DIR / "CANSLIM_模块需求说明书.pdf"),
]

COL_MAP = {
    "code": "股票代码",
    "name": "股票名称",
    "pool": "所属股票池",
    "X_of_4": "总分X/4",
    "known_of_4": "有效评分数",
    "C_pass": "C是否通过",
    "C_value": "C扣非净利润同比",
    "C_detail": "C明细",
    "A_pass": "A是否通过",
    "A_net_yoy": "A净利同比",
    "A_roe": "A_ROE(%)",
    "A_detail": "A明细",
    "S_pass": "S是否通过",
    "S_value": "S量比",
    "S_detail": "S明细",
    "L_pass": "L是否通过",
    "L_value": "L六个月超额收益",
    "L_detail": "L明细",
    "M_gate": "M大盘闸门",
    "M_detail": "M明细",
    "near_52w_high": "现价相对近52周高",
    "last_close_qfq": "最新前复权收盘价",
    "qfq_warnings": "前复权告警",
    "error": "错误信息",
}


def md_to_plain_lines(text: str) -> list[tuple[str, str]]:
    """粗解析 markdown → (style, text)。style: h1/h2/h3/code/body"""
    lines_out: list[tuple[str, str]] = []
    in_code = False
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            lines_out.append(("code", line))
            continue
        if line.startswith("# "):
            lines_out.append(("h1", line[2:].strip()))
        elif line.startswith("## "):
            lines_out.append(("h2", line[3:].strip()))
        elif line.startswith("### "):
            lines_out.append(("h3", line[4:].strip()))
        elif line.startswith("> "):
            lines_out.append(("body", line[2:].strip()))
        elif re.match(r"^\|.*\|$", line) and not re.match(r"^\|?\s*-+", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            lines_out.append(("body", " | ".join(cells)))
        elif re.match(r"^\|?\s*-+", line):
            continue
        else:
            # 去掉简单 markdown 标记
            t = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
            t = re.sub(r"`([^`]+)`", r"\1", t)
            t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
            lines_out.append(("body", t))
    return lines_out


class DocPDF(FPDF):
    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("yahei", size=9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"第 {self.page_no()} 页", align="C")


def _soft_wrap(text: str, width: int = 42) -> str:
    """避免超长无空格行导致 fpdf 无法排版。"""
    if not text:
        return text
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if len(buf) >= width and ch in " |，。；、,/\\":
            parts.append(buf)
            buf = ""
        elif len(buf) >= width:
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    return "\n".join(parts)


def export_pdf(md_path: Path, pdf_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    blocks = md_to_plain_lines(text)
    pdf = DocPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    pdf.add_font("yahei", fname=FONT)
    pdf.add_page()
    usable = pdf.epw
    for style, content in blocks:
        if not content.strip() and style == "body":
            pdf.ln(3)
            continue
        content = _soft_wrap(content, 46 if style != "code" else 52)
        # 若当前 X 已贴近右缘，换行再写
        if pdf.get_x() > pdf.l_margin + 1:
            pdf.ln(1)
        try:
            if style == "h1":
                pdf.set_font("yahei", size=16)
                pdf.multi_cell(usable, 10, content)
                pdf.ln(2)
            elif style == "h2":
                pdf.set_font("yahei", size=13)
                pdf.multi_cell(usable, 8, content)
                pdf.ln(1)
            elif style == "h3":
                pdf.set_font("yahei", size=11)
                pdf.multi_cell(usable, 7, content)
            elif style == "code":
                pdf.set_font("yahei", size=8)
                pdf.set_text_color(40, 40, 40)
                pdf.multi_cell(usable, 5, content)
                pdf.set_text_color(0, 0, 0)
            else:
                pdf.set_font("yahei", size=10)
                pdf.multi_cell(usable, 6, content)
        except Exception as e:
            # 单行失败时降级为逐段短行
            pdf.set_font("yahei", size=9)
            for piece in content.split("\n"):
                for i in range(0, max(len(piece), 1), 40):
                    pdf.multi_cell(usable, 5, piece[i : i + 40] or " ")
            print(f"[pdf-warn] {md_path.name}: {type(e).__name__}: {e}")
    pdf.output(str(pdf_path))
    print(f"[pdf] {pdf_path}")


def _bool_cn(v) -> str:
    if v is True or str(v).lower() == "true":
        return "是"
    if v is False or str(v).lower() == "false":
        return "否"
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return "缺数"
    return str(v)


def build_detail_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "code" in out.columns:
        out["code"] = out["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    for col in ("C_pass", "A_pass", "S_pass", "L_pass"):
        if col in out.columns:
            out[col] = out[col].map(_bool_cn)
    # 百分比可读列（保留原值另附）
    if "C_value" in out.columns:
        out["C扣非同比_显示"] = out["C_value"].apply(
            lambda x: f"{float(x):.2%}" if pd.notna(x) and str(x) != "" else ""
        )
    if "A_net_yoy" in out.columns:
        out["A净利同比_显示"] = out["A_net_yoy"].apply(
            lambda x: f"{float(x):.2%}" if pd.notna(x) and str(x) != "" else ""
        )
    if "L_value" in out.columns:
        out["L超额_显示"] = out["L_value"].apply(
            lambda x: f"{float(x):.2%}" if pd.notna(x) and str(x) != "" else ""
        )
    rename = {k: v for k, v in COL_MAP.items() if k in out.columns}
    out = out.rename(columns=rename)
    # 列顺序
    preferred = [
        "股票代码",
        "股票名称",
        "所属股票池",
        "总分X/4",
        "有效评分数",
        "C是否通过",
        "C扣非净利润同比",
        "C扣非同比_显示",
        "C明细",
        "A是否通过",
        "A净利同比",
        "A净利同比_显示",
        "A_ROE(%)",
        "A明细",
        "S是否通过",
        "S量比",
        "S明细",
        "L是否通过",
        "L六个月超额收益",
        "L超额_显示",
        "L明细",
        "M大盘闸门",
        "M明细",
        "现价相对近52周高",
        "最新前复权收盘价",
        "前复权告警",
        "错误信息",
    ]
    cols = [c for c in preferred if c in out.columns] + [
        c for c in out.columns if c not in preferred
    ]
    return out[cols]


def style_header(ws) -> None:
    fill = PatternFill("solid", fgColor="1F4E79")
    font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(wrap_text=True, vertical="center")


def autosize(ws, max_width: int = 48) -> None:
    for idx, col in enumerate(ws.columns, 1):
        letter = get_column_letter(idx)
        maxlen = 0
        for cell in col[:200]:
            v = "" if cell.value is None else str(cell.value)
            maxlen = max(maxlen, min(len(v), max_width))
        ws.column_dimensions[letter].width = max(10, maxlen + 2)


def export_excel(csv_path: Path, xlsx_path: Path) -> None:
    if not csv_path.exists():
        raise FileNotFoundError(f"缺少全池结果: {csv_path}")
    raw = pd.read_csv(csv_path, dtype={"code": str})
    detail = build_detail_df(raw)
    detail = detail.sort_values(
        by=[c for c in ["总分X/4", "L六个月超额收益"] if c in detail.columns],
        ascending=[False, False],
        na_position="last",
    ).reset_index(drop=True)

    wb = Workbook()

    # 说明
    ws0 = wb.active
    ws0.title = "说明"
    notes = [
        ["CANSLIM 全池评分明细"],
        ["数据来源", str(csv_path)],
        ["股票池", "沪深300 ∪ 中证1000"],
        ["总分 X/4", "C·A·S·L 通过个数；缺数不计为 0"],
        ["C", "扣非净利润同比 ≥ 25%"],
        ["A", "净利同比 ≥ 25% 且 ROE ≥ 15%"],
        ["S", "近20日均量 / 此前60日均量 ≥ 1.2"],
        ["L", "近约6月相对沪深300超额 ≥ 20%"],
        ["M", "大盘闸门 green/yellow/red，不进总分"],
        ["复权", "新浪分红表自建前复权后计算 S/L"],
        ["注意", "非投资建议；M=red 时仅作观察"],
    ]
    for r in notes:
        ws0.append(r)
    ws0["A1"].font = Font(bold=True, size=14)
    autosize(ws0)

    def add_sheet(name: str, frame: pd.DataFrame) -> None:
        ws = wb.create_sheet(name)
        for row in dataframe_to_rows(frame, index=False, header=True):
            ws.append(row)
        style_header(ws)
        ws.freeze_panes = "A2"
        autosize(ws)
        ws.auto_filter.ref = ws.dimensions

    add_sheet("全池明细", detail)
    if "总分X/4" in detail.columns:
        add_sheet("X大于等于3", detail[detail["总分X/4"].fillna(-1) >= 3].copy())
        add_sheet("X等于4", detail[detail["总分X/4"] == 4].copy())

    # 字段字典
    ws_d = wb.create_sheet("字段说明")
    ws_d.append(["英文字段", "中文列名", "含义"])
    for k, v in COL_MAP.items():
        ws_d.append([k, v, ""])
    style_header(ws_d)
    autosize(ws_d)

    wb.save(xlsx_path)
    print(f"[xlsx] {xlsx_path} rows={len(detail)}")


def main() -> None:
    for md, pdf in MD_FILES:
        export_pdf(md, pdf)
    export_excel(CSV_FULL, OUT_DIR / "CANSLIM_全池评分明细.xlsx")


if __name__ == "__main__":
    main()
