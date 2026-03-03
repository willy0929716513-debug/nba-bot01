import requests
import os
from datetime import datetime

# ===== NBA V22.1 Elite Stability =====

STRICT_EDGE_BASE = 0.020
TOTAL_EDGE_BASE = 0.022

SPREAD_COEF = 0.16
DEEP_SPREAD_COEF = 0.13

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"


# ===== 動態 Kelly =====
def kelly(prob, odds, edge):
    if odds <= 1:
        return 0

    b = odds - 1
    raw = (prob * b - (1 - prob)) / b

    if edge >= 0.05:
        cap = 0.035
    elif edge >= 0.035:
        cap = 0.030
    else:
        cap = 0.025

    return min(max(0, raw), cap)


# ===== 分級 =====
def grade(edge):
    if edge >= 0.05:
        return "🔥 S級"
    elif edge >= 0.035:
        return "⭐ A級"
    else:
        return "✅ 合格"


# ===== 深盤非線性懲罰 =====
def spread_penalty(pt):
    abs_pt = abs(pt)

    if abs_pt <= 10:
        return abs_pt * 0.0028
    elif abs_pt <= 14:
        return abs_pt * 0.0035
    else:
        return (abs_pt * 0.0035) ** 1.12


# ===== 主程式 =====
def main():
    try:
        res = requests.get(
            BASE_URL,
            params={
                "apiKey": API_KEY,
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "decimal"
            },
            timeout=10
        )

        if res.status_code != 200:
            return

        games = res.json()

    except:
        return

    results = {}

    for g in games:

        home = g["home_team"]
        away = g["away_team"]
        game_key = f"{away} @ {home}"

        markets = g.get("bookmakers", [{}])[0].get("markets", [])
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)
        totals = next((m["outcomes"] for m in markets if m["key"] == "totals"), None)

        if not h2h:
            continue

        # ===== 基礎勝率 =====
        h_ml = next(o["price"] for o in h2h if o["name"] == home)
        a_ml = next(o["price"] for o in h2h if o["name"] == away)

        p_home = (1 / h_ml) / ((1 / h_ml) + (1 / a_ml))
        strength_gap = abs(p_home - 0.5)

        best_pick = {"edge": -1}

        # ===== 讓分 =====
        if spreads:
            for o in spreads:
                pt = o["point"]
                odds = o["price"]
                is_home = o["name"] == home

                base_p = p_home if is_home else (1 - p_home)
                bias = base_p - 0.5

                coef = DEEP_SPREAD_COEF if abs(pt) > 12 else SPREAD_COEF
                p_spread = 0.5 + bias * coef + bias * 0.06

                penalty = spread_penalty(pt)

                raw_diff = p_spread - (1 / odds)
                edge = raw_diff * (1 - penalty) if raw_diff > 0 else raw_diff

                if edge > best_pick["edge"]:
                    best_pick = {
                        "pick": f"🎯 Spread {pt:+} {o['name']}",
                        "odds": odds,
                        "edge": edge,
                        "prob": p_spread,
                        "type": "spread"
                    }

        # ===== 大小分 =====
        if totals:
            for o in totals:
                line = o["point"]
                odds = o["price"]

                if o["name"] == "Over":
                    p_total = 0.5 + strength_gap * 0.06
                else:
                    p_total = 0.5 - strength_gap * 0.04

                # 方向分離盤口修正
                line_shift = (line - 228)

                if o["name"] == "Over":
                    p_total -= line_shift * 0.0013
                else:
                    p_total += line_shift * 0.0010

                raw_diff = p_total - (1 / odds)
                edge = raw_diff * (1 - 0.018) if raw_diff > 0 else raw_diff

                if edge > best_pick["edge"]:
                    best_pick = {
                        "pick": f"🏀 {o['name']} {line}",
                        "odds": odds,
                        "edge": edge,
                        "prob": p_total,
                        "type": "total"
                    }

        # ===== 嚴格過濾 =====
        if best_pick["edge"] < STRICT_EDGE_BASE:
            continue

        k = kelly(best_pick["prob"], best_pick["odds"], best_pick["edge"])
        if k <= 0:
            continue

        best_pick["k"] = k
        results[game_key] = best_pick

    # ===== 排序只出 2 場 =====
    sorted_res = sorted(results.items(), key=lambda x: x[1]["edge"], reverse=True)[:2]

    # ===== 風險集中提醒 =====
    market_types = [p["type"] for _, p in sorted_res]

    if len(market_types) == 2 and market_types[0] == market_types[1]:
        if market_types[0] == "total":
            hedge_note = "⚠️ 兩場皆大小分，留意節奏波動"
        else:
            hedge_note = "⚠️ 兩場皆讓分，留意垃圾時間"
    else:
        hedge_note = "🛡️ 市場分散良好"

    # ===== 輸出 =====
    msg = f"🛰️ NBA V22.1 Elite Stability\n"
    msg += f"{datetime.now().strftime('%m/%d %H:%M')}\n\n"

    for g, p in sorted_res:
        msg += f"__{g}__\n"
        msg += f"{grade(p['edge'])} 👉 {p['pick']}\n"
        msg += f"Edge: {p['edge']:.2%} | Odds: {p['odds']} | Kelly: {p['k']:.2%}\n"
        msg += "-----\n"

    msg += f"\n{hedge_note}"

    requests.post(WEBHOOK_URL, json={"content": msg})


if __name__ == "__main__":
    main()
