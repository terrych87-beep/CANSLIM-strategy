"""CANSLIM 阈值与运行参数（与 docs/plans/CANSLIM_落地步骤.md 对齐）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"

# 基准
BENCHMARK_SYMBOL = "sh000300"  # 沪深300
HS300_INDEX_ID = "000300"
CSI1000_INDEX_ID = "000852"

# 因子阈值（用户确认）
C_KF_YOY_MIN = 0.25
A_NET_YOY_MIN = 0.25
A_ROE_MIN = 15.0  # 百分比数值，如 15.0 表示 15%
S_VOL_RATIO_MIN = 1.2
L_EXCESS_6M_MIN = 0.20

# 日 K
KLINE_DATALEN = 800  # 约 3 年+；覆盖 6M/52w，并让窗口内除权可复权
VOL_SHORT = 20
VOL_LONG_PRIOR = 60  # 近20之前的60日
LOOKBACK_6M_BARS = 126  # 约半年交易日
MA_FAST = 50
MA_SLOW = 200

# HTTP
HTTP_TIMEOUT = 30
HTTP_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
REQUEST_SLEEP_SEC = 0.15  # 批量限速（全池 + 重试）


@dataclass(frozen=True)
class Thresholds:
    c_kf_yoy_min: float = C_KF_YOY_MIN
    a_net_yoy_min: float = A_NET_YOY_MIN
    a_roe_min: float = A_ROE_MIN
    s_vol_ratio_min: float = S_VOL_RATIO_MIN
    l_excess_6m_min: float = L_EXCESS_6M_MIN
