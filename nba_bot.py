import requests
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ===== NBA V19.6 Context-Aware =====
STRICT_EDGE_BASE = 0.022     # 讓分門檻提高
TOTAL_EDGE_BASE = 0.025      # 大小分門檻提高
KELLY_CAP = 0.025

SPREAD_COEF = 0.16
DEEP_SPREAD_COEF = 0.14
BUY_POINT_FACTOR = 0.90

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

TEAM_CN = {
    "Los Angeles Lakers": "湖人","Golden State Warriors": "勇士","Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿","Denver Nuggets": "金塊","Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽","LA Clippers": "快艇","Miami Heat": "熱火",
    "Philadelphia 76ers": "七六人","Sacramento Kings": "國王","New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼","Dallas Mavericks": "獨行俠","New York Knicks": "尼克",
    "Orlando Magic": "魔術","Charlotte Hornets": "黃蜂","Detroit Pistons": "活塞",
    "Toronto Raptors": "暴龍","Chicago Bulls": "公牛","San Antonio Spurs": "馬刺",
    "Utah Jazz": "爵士","Brooklyn Nets": "籃網","Atlanta Hawks": "老鷹",
    "Cleveland Cavaliers": "騎士","Indiana Pacers": "溜馬","Memphis Grizzlies": "灰熊",
    "Portland Trail Blazers": "拓荒者","Washington Wizards": "巫師","Houston Rockets": "火箭"
}

def cn(t): return TEAM_CN.get(t, t)

def kelly(prob, odds):
    if odds <= 1: return 0
    b = odds - 1
    raw = (prob*b - (1-prob)) / b
    return min(max(0, raw), KELLY_CAP)

def get_contextual_insight(pick_type, edge, is_unqualified=False):
    if "🏀" in pick_type:
        return "📊 極端盤口均值回歸模型觸發。"
    if "買分" in pick_type and edge > 0.025:
        return "🛡️ 關鍵區間買分，提高安全邊際。"
    if is_unqualified:
        return "⚠️ 盤口接近合理區間，僅供觀察。"
    return "✨ 市場定價與模型存在偏差。"

def main():
    try:
        res = requests.get(BASE_URL, params={
            "apiKey": API_KEY,
            "regions": "us",
            "markets": "h2h,spreads,totals",
            "oddsFormat": "decimal"
        })
        games = res.json()
    except:
        return

    game_analysis = defaultdict(dict)

    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)

        home_en, away_en = g["home_team"], g["away_team"]
        game_key = f"{cn(away_en)} @ {cn(home_en)}"

        markets = g.get("bookmakers", [{}])[0].get("markets", [])
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)
        totals = next((m["outcomes"] for m in markets if m["key"] == "totals"), None)
        if not h2h: continue

        # ===== 計算真實勝率 =====
        h_ml = next(o["price"] for o in h2h if o["name"] == home_en)
        a_ml = next(o["price"] for o in h2h if o["name"] == away_en)
        p_home = (1/h_ml) / ((1/h_ml) + (1/a_ml))

        # =====================
        # ===== 讓分模型 ======
        # =====================
        best_spread = {"edge": -1}

        if spreads:
            for o in spreads:
                pt, odds = o["point"], o["price"]
                base_p = p_home if o["name"] == home_en else (1-p_home)
                coef = DEEP_SPREAD_COEF if abs(pt) > 12 else SPREAD_COEF
                p_spread = 0.5 + ((base_p - 0.5) * coef)

                penalty = 0.012 if abs(pt) > 10 else 0.008

                if 7 <= abs(pt) <= 11:
                    f_p = p_spread + 0.015
                    f_odds = odds * BUY_POINT_FACTOR
                    f_pt = pt + 1.5 if pt < 0 else pt - 1.5
                    label = "🛡️ 讓分買分"
                else:
                    f_p = p_spread
                    f_odds = odds
                    f_pt = pt
                    label = "🎯 讓分"

                edge = f_p - (1/f_odds) - penalty

                if edge > best_spread["edge"]:
                    best_spread = {
                        "pick": f"{label}({f_pt:+})：{cn(o['name'])}",
                        "odds": round(f_odds,2),
                        "edge": edge,
                        "k": kelly(f_p, f_odds)
                    }

        # =====================
        # ===== Totals 模型 ====
        # =====================
        best_total = {"edge": -1}

        if totals:
            for o in totals:
                line, odds = o["point"], o["price"]

                if odds <= 1:
                    continue

                p_total = 0.5

                # ---- 極端盤口均值回歸 ----
                if o["name"] == "Under":
                    if line >= 236:
                        p_total += 0.02
                    elif line >= 232:
                        p_total += 0.01

                elif o["name"] == "Over":
                    if line <= 214:
                        p_total += 0.02
                    elif line <= 218:
                        p_total += 0.01

                penalty = 0.018
                edge = p_total - (1/odds) - penalty

                if edge > best_total["edge"]:
                    best_total = {
                        "pick": f"🏀 {o['name']} {line}",
                        "odds": odds,
                        "edge": edge,
                        "k": kelly(p_total, odds)
                    }

        # =====================
        # ===== 單場最佳選擇 ==
        # =====================
        final_pick = {"edge": -1}

        if best_total["edge"] > best_spread["edge"] and best_total["edge"] >= TOTAL_EDGE_BASE:
            final_pick = best_total
        elif best_spread["edge"] >= STRICT_EDGE_BASE:
            final_pick = best_spread
        else:
            final_pick = best_total if best_total["edge"] > best_spread["edge"] else best_spread
            final_pick["is_unqualified"] = True

        game_analysis[game_key] = final_pick

    # ===== 輸出排序 =====
    sorted_games = sorted(game_analysis.items(), key=lambda x: x[1]["edge"], reverse=True)

    msg = f"🛰️ NBA V19.6 Context-Aware - {datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += "*(邏輯：極端盤口均值回歸 > 讓分模型 > 顯示最佳)*\n"

    for game_key, pick in sorted_games[:4]:
        if pick["edge"] < 0:
            continue

        status = "⚠️ [觀察]" if pick.get("is_unqualified") else "✅"
        insight = get_contextual_insight(pick["pick"], pick["edge"], pick.get("is_unqualified", False))

        msg += f"\n{status} __**{game_key}**__ | **{pick['pick']}**\n"
        msg += f"  └ Edge: `{pick['edge']:.2%}` | 賠率: `{pick['odds']}` | 倉: `{pick['k']:.2%}`\n"
        msg += f"  └ *{insight}*\n"
        msg += "---"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
