import requests
import os
from datetime import datetime, timedelta

# ===== NBA V17.3 Transparency 參數 =====
STRICT_EDGE_BASE = 0.018
BRIDGE_EDGE_MIN = 0.014
KELLY_CAP = 0.045
SPREAD_COEF = 0.19
BUY_POINT_FACTOR = 0.91
ODDS_MIN, ODDS_MAX = 1.35, 3.50

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
    raw = (prob * b - (1 - prob)) / b
    return min(max(0, raw), KELLY_CAP)

def get_penalty(point):
    return 0.015 if abs(point) > 12 else 0.008

def main():
    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"})
        games = res.json()
    except: return

    # 分類列表
    qualified_picks = []  # 達標
    potential_picks = []  # 觀察中 (Edge > 0)

    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        home_en, away_en = g["home_team"], g["away_team"]
        
        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        markets = bookmakers[0].get("markets", [])
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)
        if not h2h or not spreads: continue

        h_ml = next(o["price"] for o in h2h if o["name"] == home_en)
        a_ml = next(o["price"] for o in h2h if o["name"] == away_en)
        p_home_real = (1/h_ml) / ((1/h_ml) + (1/a_ml))

        for o in spreads:
            pt, odds = o["point"], o["price"]
            abs_pt = abs(pt)
            if not (ODDS_MIN <= odds <= ODDS_MAX): continue

            base_p = p_home_real if o["name"] == home_en else (1 - p_home_real)
            coef = 0.17 if abs_pt > 12 else SPREAD_COEF
            p_spread = 0.5 + ((base_p - 0.5) * coef)
            original_edge = p_spread - (1/odds)

            # 邏輯判斷
            if 7 <= abs_pt <= 11 and original_edge >= 0.005:
                final_pt, final_odds, final_p = pt + 1.5 if pt < 0 else pt - 1.5, odds * BUY_POINT_FACTOR, p_spread + 0.045
                penalty, threshold, label = 0.005, BRIDGE_EDGE_MIN, "🛡️ 買分"
            else:
                final_pt, final_odds, final_p = pt, odds, p_spread
                penalty, threshold, label = get_penalty(pt), STRICT_EDGE_BASE, "🎯 原始"

            edge = final_p - (1/final_odds) - penalty
            k = kelly(final_p, final_odds)

            pick_data = {
                "game": f"{cn(away_en)} @ {cn(home_en)}",
                "pick": f"{label}({final_pt:+})：{cn(o['name'])}",
                "odds": round(final_odds, 2), "edge": edge, "kelly": k
            }

            if edge >= threshold:
                qualified_picks.append(pick_data)
            elif edge > 0:
                potential_picks.append(pick_data)

    # --- 輸出訊息 ---
    msg = f"🛰️ NBA V17.3 Transparency - {datetime.now().strftime('%m/%d %H:%M')}\n"
    
    msg += "\n✅ **【重點獲利場次】**\n"
    if not qualified_picks:
        msg += "> 目前無場次達標\n"
    else:
        for r in sorted(qualified_picks, key=lambda x: x['edge'], reverse=True):
            msg += f"• {r['game']} | **{r['pick']}** | 賠率:{r['odds']} | Edge:{r['edge']:.2%}\n"

    msg += "\n⚠️ **【觀察/小注場次】** (Edge > 0)\n"
    if not potential_picks:
        msg += "> 目前無潛在優勢場次\n"
    else:
        for r in sorted(potential_picks, key=lambda x: x['edge'], reverse=True)[:5]: # 只顯示前五場
            msg += f"• {r['game']} | {r['pick']} | Edge:{r['edge']:.2%}\n"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
