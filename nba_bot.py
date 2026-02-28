import requests
import os
from datetime import datetime, timedelta

# ===== V16.4 Safe Bridge 參數 =====
STRICT_EDGE_BASE = 0.020    # 深盤/小盤門檻
BRIDGE_EDGE_MIN = 0.015     # 買分避險單門檻 (因賠率低，門檻微降)
KELLY_CAP = 0.05
SPREAD_COEF = 0.20
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

def get_penalty(point):
    abs_pt = abs(point)
    if abs_pt > 15: return 0.025  # 深盤維持樂觀
    return 0.010                  # 基本防禦

def main():
    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"})
        games = res.json()
    except: return

    picks = []
    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        
        home_en, away_en = g["home_team"], g["away_team"]
        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        m_list = bookmakers[0].get("markets", [])
        h2h = next((m["outcomes"] for m in m_list if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in m_list if m["key"] == "spreads"), None)
        if not h2h: continue

        h_ml, a_ml = next(o for o in h2h if o["name"] == home_en)["price"], next(o for o in h2h if o["name"] == away_en)["price"]
        p_home = min((1/h_ml) / ((1/h_ml) + (1/a_ml)) + 0.02, 0.95)
        p_away = 1 - p_home

        if spreads:
            for o in spreads:
                pt, odds = o["point"], o["price"]
                abs_pt = abs(pt)
                
                # --- V16.4 Safe Bridge 邏輯 ---
                if 7.0 <= abs_pt <= 11.0:
                    # 強制買 1.5 分避險
                    final_pt = pt + 1.5 if pt < 0 else pt - 1.5
                    final_odds = odds - 0.21  # 買 1.5 分賠率大幅下滑
                    penalty = 0.005           # 買分後風險降低
                    threshold = BRIDGE_EDGE_MIN
                    label = "🛡️ 避險買分"
                else:
                    final_pt = pt
                    final_odds = odds
                    penalty = get_penalty(pt)
                    threshold = STRICT_EDGE_BASE
                    label = "🎯 原始盤口"

                p_spread = 0.5 + ((p_home if o["name"] == home_en else p_away) - 0.5) * SPREAD_COEF
                edge = p_spread - (1/final_odds) - penalty
                
                if edge >= threshold and ODDS_MIN <= final_odds <= ODDS_MAX:
                    picks.append({
                        "game": f"{cn(away_en)} @ {cn(home_en)}",
                        "date": tw_time.strftime('%m/%d'),
                        "pick": f"{label}({final_pt:+})：{cn(o['name'])}",
                        "odds": final_odds, "edge": edge, "prob": p_spread
                    })

    msg = f"🛰️ NBA V16.4 Safe Bridge - {datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += f"*(策略：7-11分區間強制買1.5分避險)*\n"

    if not picks:
        msg += "\n🚫 今日所有場次（含買分避險）均無足夠優勢。"
    else:
        for r in sorted(picks, key=lambda x: x["edge"], reverse=True):
            msg += f"\n📅 {r['date']} | **{r['game']}**\n"
            msg += f"> 💰 {r['pick']} | 賠率：{r['odds']:.2f}\n"
            msg += f"> 📈 修正優勢：{r['edge']:.2%} | 倉位：{max(0, (r['prob']*(r['odds']-1)-(1-r['prob']))/(r['odds']-1))*100:.2f}%\n"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
