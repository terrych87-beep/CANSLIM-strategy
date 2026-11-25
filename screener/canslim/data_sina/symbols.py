"""股票/指数代码规范化。"""

from __future__ import annotations

# 上交所指数（与个股 000xxx 深市不同，调用方应直接传 sh000300 等）
_SH_INDEX = {
    "000001",  # 上证指数（若当指数用需显式 sh）
    "000016",
    "000300",
    "000688",
    "000852",
    "000905",
    "000903",
    "000010",
}


def to_digits(code: str) -> str:
    c = code.strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c[2:8]
    digits = "".join(ch for ch in c if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"无法解析六位码: {code!r}")
    return digits


def to_sina_symbol(code: str, *, as_index: bool = False) -> str:
    """六位码或已带前缀 → 新浪 symbol。

    个股规则：6/9→sh；0/1/2/3→sz；4/8→bj。
    指数请传 `sh000300` 或 `as_index=True`（仅对已知上交所指数码）。
    """
    c = code.strip().lower()
    if c.startswith(("sh", "sz", "bj")) and len(c) >= 8:
        return c[:8]
    digits = to_digits(c)
    if as_index or (digits in _SH_INDEX and as_index):
        return f"sh{digits}"
    if digits.startswith(("5", "6", "9")):
        return f"sh{digits}"
    if digits.startswith(("4", "8")):
        return f"bj{digits}"
    return f"sz{digits}"
