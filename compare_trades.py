# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：默认相对项目根目录的 JQ_PATH / LOCAL_PATH；对账 run 可传 argv[1]
# 输出参数：stdout 对账统计与差异明细
# 用途：聚宽 transaction.csv ↔ 本地 trades.csv 逐笔对比（2021 起对账基线）
# 对账口径：MACD 以本地 MacdDivCfg 为准（见 .cursor/rules/local-macd-reconciliation-baseline.mdc）
# 使用说明：python compare_trades.py  或改 LOCAL_PATH / 后续加 CLI

import pandas as pd
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

JQ_PATH = ROOT / "transaction.csv"
LOCAL_PATH = ROOT / "outputs" / "backtest" / "run_20260604_155739" / "trades.csv"
JQ_LOG = ROOT / "基于中枢的macd交易逻辑交易策略验证日志.txt"
LOCAL_LOG = ROOT / "outputs" / "backtest" / "run_20260604_155739" / "run_log.log"
PRICE_TOL = 0.5  # 允许的小幅价格差异（点）

TYPE_MAP = {
    "开多": ("open", "long"),
    "平多": ("close", "long"),
    "开空": ("open", "short"),
    "平空": ("close", "short"),
}


def parse_jq_time(row):
    date_str = str(row["日期"]).strip()
    time_str = str(row["委托时间"]).strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            pass
    raise ValueError(f"Cannot parse JQ time: {date_str} {time_str}")


def parse_local_time(s):
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"Cannot parse local time: {s}")


def extract_contract_jq(s):
    m = re.search(r"(IF\d{4})", str(s))
    return m.group(1) if m else str(s)


def normalize_jq(df):
    rows = []
    for _, r in df.iterrows():
        action, direction = TYPE_MAP[r["交易类型"]]
        dt = parse_jq_time(r)
        rows.append({
            "dt": dt,
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M"),
            "action": action,
            "direction": direction,
            "contract": extract_contract_jq(r["标的"]),
            "price": float(r["成交价"]),
            "qty": abs(int(str(r["成交数量"]).replace("手", "").replace("-", ""))),
            "source": "jq",
            "raw_type": r["交易类型"],
        })
    return pd.DataFrame(rows).sort_values("dt").reset_index(drop=True)


def normalize_local(df):
    rows = []
    for _, r in df.iterrows():
        dt = parse_local_time(r["交易时间"])
        rows.append({
            "dt": dt,
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M"),
            "action": r["动作"],
            "direction": r["方向"],
            "contract": str(r["合约"]).strip(),
            "price": float(r["成交价"]),
            "qty": int(r["手数"]),
            "reason": str(r["原因"]),
            "pos_type": str(r.get("持仓类型", "")),
            "source": "local",
        })
    return pd.DataFrame(rows).sort_values("dt").reset_index(drop=True)


def trade_key(t):
    return (t["dt"], t["action"], t["direction"])


def compare_fields(jq_t, loc_t):
    diffs = []
    if jq_t["contract"] != loc_t["contract"]:
        diffs.append(f"合约: 聚宽={jq_t['contract']} vs 本地={loc_t['contract']}")
    if abs(jq_t["price"] - loc_t["price"]) > PRICE_TOL:
        diffs.append(f"价格: 聚宽={jq_t['price']} vs 本地={loc_t['price']} (差{loc_t['price']-jq_t['price']:+.1f})")
    if jq_t["qty"] != loc_t["qty"]:
        diffs.append(f"手数: 聚宽={jq_t['qty']} vs 本地={loc_t['qty']}")
    return diffs


def find_log_reason(log_path, dt, action, is_jq=True):
    """Extract reason from log near trade time."""
    date_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%H:%M")
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return None

    hits = []
    for i, line in enumerate(lines):
        if date_str not in line:
            continue
        if time_str not in line:
            continue
        if is_jq:
            if action == "open" and ("开仓" in line or "开多" in line or "开空" in line or "order" in line.lower()):
                hits.append(line.strip())
            elif action == "close" and ("平仓" in line or "平多" in line or "平空" in line):
                hits.append(line.strip())
        else:
            if action == "open" and ("动作=open" in line or "原因=" in line):
                hits.append(line.strip())
            elif action == "close" and ("动作=close" in line):
                hits.append(line.strip())
    return hits[:3] if hits else None


def main():
    jq_df = normalize_jq(pd.read_csv(JQ_PATH, encoding="utf-8"))
    loc_df = normalize_local(pd.read_csv(LOCAL_PATH, encoding="utf-8"))

    print("=" * 80)
    print(f"聚宽交易: {len(jq_df)} 笔 | 本地交易: {len(loc_df)} 笔")
    print("=" * 80)

    jq_i, loc_i = 0, 0
    sync_count = 0
    divergence_num = 0
    matched_count = 0

    while jq_i < len(jq_df) and loc_i < len(loc_df):
        jq_t = jq_df.iloc[jq_i]
        loc_t = loc_df.iloc[loc_i]

        # 尝试按时间对齐（允许同一时刻多笔，先比 action+direction）
        if jq_t["dt"] == loc_t["dt"] and jq_t["action"] == loc_t["action"] and jq_t["direction"] == loc_t["direction"]:
            diffs = compare_fields(jq_t, loc_t)
            if diffs:
                divergence_num += 1
                print(f"\n【分叉 #{divergence_num}】时间对齐但字段不一致 @ {jq_t['dt']}")
                print(f"  聚宽: {jq_t['raw_type']} {jq_t['contract']} @{jq_t['price']} {jq_t['qty']}手")
                print(f"  本地: {loc_t['action']}/{loc_t['direction']} {loc_t['contract']} @{loc_t['price']} {loc_t['qty']}手 原因={loc_t['reason']}")
                for d in diffs:
                    print(f"    - {d}")
            else:
                matched_count += 1
            jq_i += 1
            loc_i += 1
            continue

        # 时间相同但动作不同
        if jq_t["dt"] == loc_t["dt"]:
            divergence_num += 1
            print(f"\n【分叉 #{divergence_num}】同时间不同动作 @ {jq_t['dt']}")
            print(f"  聚宽: {jq_t['raw_type']} {jq_t['contract']} @{jq_t['price']}")
            print(f"  本地: {loc_t['action']}/{loc_t['direction']} {loc_t['contract']} @{loc_t['price']} 原因={loc_t['reason']}")
            # 找下一个时间相同的点
            target_dt = None
            for k in range(loc_i + 1, len(loc_df)):
                for j in range(jq_i + 1, len(jq_df)):
                    if loc_df.iloc[k]["dt"] == jq_df.iloc[j]["dt"]:
                        lt, jt = loc_df.iloc[k], jq_df.iloc[j]
                        if lt["action"] == jt["action"] and lt["direction"] == jt["direction"]:
                            target_dt = lt["dt"]
                            break
                if target_dt:
                    break
            if target_dt:
                print(f"  → 下一同步点: {target_dt}")
                while jq_i < len(jq_df) and jq_df.iloc[jq_i]["dt"] != target_dt:
                    skipped = jq_df.iloc[jq_i]
                    print(f"    聚宽独有(跳过): {skipped['dt']} {skipped['raw_type']} {skipped['contract']}")
                    jq_i += 1
                while loc_i < len(loc_df) and loc_df.iloc[loc_i]["dt"] != target_dt:
                    skipped = loc_df.iloc[loc_i]
                    print(f"    本地独有(跳过): {skipped['dt']} {skipped['action']}/{skipped['direction']} {skipped['contract']} 原因={skipped['reason']}")
                    loc_i += 1
                sync_count += 1
            else:
                jq_i += 1
                loc_i += 1
            continue

        # 时间不同 - 找谁先
        if jq_t["dt"] < loc_t["dt"]:
            divergence_num += 1
            print(f"\n【分叉 #{divergence_num}】聚宽领先 @ 聚宽={jq_t['dt']} vs 本地={loc_t['dt']}")
            print(f"  聚宽独有: {jq_t['raw_type']} {jq_t['contract']} @{jq_t['price']} {jq_t['qty']}手")
            # 找下一个两边时间能对上的点
            target_dt = None
            jq_search = jq_i + 1
            while jq_search < len(jq_df):
                jt = jq_df.iloc[jq_search]
                for k in range(loc_i, len(loc_df)):
                    lt = loc_df.iloc[k]
                    if lt["dt"] == jt["dt"] and lt["action"] == jt["action"] and lt["direction"] == jt["direction"]:
                        target_dt = jt["dt"]
                        loc_i = k
                        break
                if target_dt:
                    jq_i = jq_search
                    break
                jq_search += 1
            if not target_dt:
                print(f"  本地当前: {loc_t['action']}/{loc_t['direction']} {loc_t['contract']} @{loc_t['dt']} 原因={loc_t['reason']}")
                jq_i += 1
            else:
                print(f"  → 下一同步点: {target_dt}")
                while jq_i < len(jq_df) and jq_df.iloc[jq_i]["dt"] != target_dt:
                    skipped = jq_df.iloc[jq_i]
                    print(f"    聚宽跳过: {skipped['dt']} {skipped['raw_type']} {skipped['contract']}")
                    jq_i += 1
                sync_count += 1
        else:
            divergence_num += 1
            print(f"\n【分叉 #{divergence_num}】本地领先 @ 本地={loc_t['dt']} vs 聚宽={jq_t['dt']}")
            print(f"  本地独有: {loc_t['action']}/{loc_t['direction']} {loc_t['contract']} @{loc_t['price']} 原因={loc_t['reason']}")
            target_dt = None
            loc_search = loc_i + 1
            while loc_search < len(loc_df):
                lt = loc_df.iloc[loc_search]
                for j in range(jq_i, len(jq_df)):
                    jt = jq_df.iloc[j]
                    if lt["dt"] == jt["dt"] and lt["action"] == jt["action"] and lt["direction"] == jt["direction"]:
                        target_dt = lt["dt"]
                        jq_i = j
                        break
                if target_dt:
                    loc_i = loc_search
                    break
                loc_search += 1
            if not target_dt:
                print(f"  聚宽当前: {jq_t['raw_type']} {jq_t['contract']} @{jq_t['dt']}")
                loc_i += 1
            else:
                print(f"  → 下一同步点: {target_dt}")
                while loc_i < len(loc_df) and loc_df.iloc[loc_i]["dt"] != target_dt:
                    skipped = loc_df.iloc[loc_i]
                    print(f"    本地跳过: {skipped['dt']} {skipped['action']}/{skipped['direction']} {skipped['contract']} 原因={skipped['reason']}")
                    loc_i += 1
                sync_count += 1

    # 剩余
    if jq_i < len(jq_df) or loc_i < len(loc_df):
        print("\n【尾部未对齐】")
        while jq_i < len(jq_df):
            t = jq_df.iloc[jq_i]
            print(f"  聚宽剩余: {t['dt']} {t['raw_type']} {t['contract']} @{t['price']}")
            jq_i += 1
        while loc_i < len(loc_df):
            t = loc_df.iloc[loc_i]
            print(f"  本地剩余: {t['dt']} {t['action']}/{t['direction']} {t['contract']} @{t['price']} 原因={t['reason']}")
            loc_i += 1

    print("\n" + "=" * 80)
    print(f"完全匹配(时间+方向+合约+价格): {matched_count} 笔")
    print(f"分叉事件: {divergence_num} 次 | 重新同步: {sync_count} 次")
    print("=" * 80)


if __name__ == "__main__":
    main()
