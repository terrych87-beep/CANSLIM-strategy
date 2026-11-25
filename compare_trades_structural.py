# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：默认相对项目根目录；LOCAL_PATH 可传 argv[1] 指定 trades.csv
# 输出参数：stdout 结构对账（时间、方向、合约、价格、原因）
# 用途：主对账 transaction.csv ↔ trades.csv；不用聚宽 MACD 探针日志验收
# 对账口径：见 .cursor/rules/local-macd-reconciliation-baseline.mdc
# 使用说明：python compare_trades_structural.py [trades.csv路径]

import pandas as pd
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

JQ_PATH = ROOT / "transaction.csv"
LOCAL_PATH = ROOT / "outputs" / "backtest" / "run_20260604_171927" / "trades.csv"
PRICE_TOL = 0.5

TYPE_MAP = {
    "开多": ("open", "long"),
    "平多": ("close", "long"),
    "开空": ("open", "short"),
    "平空": ("close", "short"),
}
LOC_ACTION = {"开": "open", "平": "close"}
LOC_DIR = {"多": "long", "空": "short"}

# 本地原因 -> 聚宽日志常见表述
REASON_HINT = {
    "14:57_long_range_break": "震荡区间突破买点 / range突破多",
    "osc_long_by_bottom_divergence": "震荡区间内买点 / range震荡多",
    "osc_short_by_top_divergence": "震荡区间内卖点 / range震荡空",
    "range_break_sell_confirmed": "震荡区间突破卖点 / range下破",
    "long_break_take_profit_5pct": "止盈(5.0%)",
    "long_break_stop_loss": "止损(1.0%)",
    "long_break_take_profit_by_top_divergence": "顶背离止盈",
    "long_osc_stop_loss": "震荡多止损",
    "long_osc_take_profit_by_range_pct": "震荡多止盈(区间幅度)",
    "long_osc_take_profit_by_top_divergence": "顶背离止盈",
    "short_osc_stop_loss": "震荡空止损",
    "short_osc_take_profit_by_range_pct": "震荡空止盈(区间幅度)",
    "short_osc_take_profit_by_bottom_divergence": "底背离止盈",
    "short_break_stop_loss": "突破空止损",
    "移仓": "移仓",
}


def parse_jq_time(row):
    date_str = str(row["日期"]).strip()
    time_str = str(row["委托时间"]).strip()
    fmt = "%Y/%m/%d %H:%M:%S" if len(time_str.split(":")) == 3 else "%Y/%m/%d %H:%M"
    return datetime.strptime(f"{date_str} {time_str}", fmt)


def parse_local_time(s):
    s = str(s).strip()
    fmt = "%Y-%m-%d %H:%M:%S" if len(s.split(":")) == 3 else "%Y-%m-%d %H:%M"
    return datetime.strptime(s, fmt)


def load():
    jq_rows = []
    for _, r in pd.read_csv(JQ_PATH, encoding="utf-8").iterrows():
        action, direction = TYPE_MAP[r["交易类型"]]
        dt = parse_jq_time(r)
        m = re.search(r"(IF\d{4})", str(r["标的"]))
        jq_rows.append({
            "dt": dt,
            "action": action,
            "direction": direction,
            "contract": m.group(1),
            "price": float(r["成交价"]),
            "qty": abs(int(str(r["成交数量"]).replace("手", "").replace("-", ""))),
            "jq_type": r["交易类型"],
        })
    loc_rows = []
    for _, r in pd.read_csv(LOCAL_PATH, encoding="utf-8").iterrows():
        reason = str(r["原因"])
        if "移仓" in reason:
            continue
        dt = parse_local_time(r["交易时间"])
        loc_rows.append({
            "dt": dt,
            "action": LOC_ACTION[str(r["动作"]).strip()],
            "direction": LOC_DIR[str(r["方向"]).strip()],
            "contract": str(r["合约"]).strip(),
            "price": float(r["成交价"]),
            "qty": int(r["手数"]),
            "reason": reason,
            "pos_type": str(r.get("持仓类型", "")),
        })
    return pd.DataFrame(jq_rows), pd.DataFrame(loc_rows)


def align_and_report(jq, loc):
    i, j = 0, 0
    seg = 1
    perfect = 0
    structural_events = []

    while i < len(jq) and j < len(loc):
        a, b = jq.iloc[i], loc.iloc[j]
        same_slot = (
            a["dt"] == b["dt"]
            and a["action"] == b["action"]
            and a["direction"] == b["direction"]
        )

        if same_slot:
            issues = []
            if a["contract"] != b["contract"]:
                issues.append(("合约", a["contract"], b["contract"]))
            if abs(a["price"] - b["price"]) > PRICE_TOL:
                issues.append(("价格", a["price"], b["price"]))
            # 理由仅本地有，记录供对照
            if issues:
                structural_events.append({
                    "seg": seg,
                    "kind": "字段偏差",
                    "dt": a["dt"],
                    "action": f"{a['action']}/{a['direction']}",
                    "jq": a,
                    "loc": b,
                    "issues": issues,
                })
            else:
                perfect += 1
            i += 1
            j += 1
            continue

        # 找下一对齐点：相同 dt+action+direction
        next_i = next_j = None
        for ii in range(i, min(i + 30, len(jq))):
            for jj in range(j, min(j + 30, len(loc))):
                x, y = jq.iloc[ii], loc.iloc[jj]
                if x["dt"] == y["dt"] and x["action"] == y["action"] and x["direction"] == y["direction"]:
                    next_i, next_j = ii, jj
                    break
            if next_i is not None:
                break

        jq_only = [jq.iloc[k] for k in range(i, next_i if next_i else min(i + 5, len(jq)))]
        loc_only = [loc.iloc[k] for k in range(j, next_j if next_j else min(j + 5, len(loc)))]

        structural_events.append({
            "seg": seg,
            "kind": "路径分叉",
            "jq": a,
            "loc": b,
            "jq_only": jq_only,
            "loc_only": loc_only,
            "resync": (jq.iloc[next_i]["dt"] if next_i else None),
        })
        seg += 1
        if next_i is None:
            i += 1
            j += 1
        else:
            i, j = next_i, next_j

    tail_jq = jq.iloc[i:].to_dict("records") if i < len(jq) else []
    tail_loc = loc.iloc[j:].to_dict("records") if j < len(loc) else []
    return perfect, structural_events, tail_jq, tail_loc


def main():
    jq, loc = load()
    perfect, events, tail_jq, tail_loc = align_and_report(jq, loc)

    print("=" * 90)
    print(f"聚宽 {len(jq)} 笔 | 本地 {len(loc)} 笔 | 时间+方向完全对齐且合约/价格一致: {perfect} 笔")
    print("=" * 90)

    # 只打印前 25 个结构性事件 + 每段 resync 摘要
    path_splits = [e for e in events if e["kind"] == "路径分叉"]
    field_diffs = [e for e in events if e["kind"] == "字段偏差"]

    print(f"\n路径分叉(时间/方向不同步): {len(path_splits)} 段")
    print(f"对齐后字段偏差(合约/价格): {len(field_diffs)} 笔\n")

    for n, e in enumerate(path_splits[:30], 1):
        jq0, loc0 = e["jq"], e["loc"]
        print(f"--- 分叉段 #{n} (段号{e['seg']}) ---")
        print(f"  聚宽当前: {jq0['dt']} {jq0['jq_type']} {jq0['contract']} @{jq0['price']} {jq0['qty']}手")
        print(f"  本地当前: {loc0['dt']} {loc0['action']}/{loc0['direction']} {loc0['contract']} @{loc0['price']} {loc0['qty']}手")
        print(f"  本地理由: {loc0['reason']} ({REASON_HINT.get(loc0['reason'], '')})")
        if e["resync"]:
            print(f"  >> 下一对齐点: {e['resync']}")
        print("  聚宽多出行:")
        for x in e["jq_only"]:
            if not (x["dt"] == jq0["dt"] and x["jq_type"] == jq0["jq_type"]):
                continue
            print(f"    - {x['dt']} {x['jq_type']} {x['contract']} @{x['price']}")
        for x in e["jq_only"]:
            if x["dt"] == jq0["dt"] and x["jq_type"] == jq0["jq_type"]:
                continue
            print(f"    - {x['dt']} {x['jq_type']} {x['contract']} @{x['price']}")
        print("  本地多出行:")
        for x in e["loc_only"]:
            print(f"    - {x['dt']} {x['action']}/{x['direction']} {x['contract']} @{x['price']} 原因={x['reason']}")
        print()

    if len(path_splits) > 30:
        print(f"... 另有 {len(path_splits)-30} 段路径分叉未展开 ...\n")

    print("--- 对齐后仅价格/合约偏差 (前15笔) ---")
    for e in field_diffs[:15]:
        jq0, loc0 = e["jq"], e["loc"]
        print(f"  {jq0['dt']} {jq0['jq_type']}/{loc0['reason']}")
        for name, v1, v2 in e["issues"]:
            print(f"    {name}: 聚宽={v1} 本地={v2}")

    if tail_jq or tail_loc:
        print("\n--- 尾部未对齐 ---")
        for x in tail_jq:
            print(f"  聚宽: {x['dt']} {x['jq_type']} {x['contract']} @{x['price']}")
        for x in tail_loc:
            print(f"  本地: {x['dt']} {x['action']}/{x['direction']} {x['contract']} @{x['price']} {x['reason']}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        LOCAL_PATH = Path(sys.argv[1])
    main()
