import requests
import os
from datetime import datetime, timedelta

# ===== V15.3 Investor 參數 =====
EDGE_THRESHOLD = 0.022
KELLY_CAP = 0.05
SPREAD_COEF = 0.22
ODDS_MIN = 1.45             
ODDS_MAX = 3.00

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
    b = odds - 1
    if prob <= 1/odds: return 0
    k = (prob * b - (1 - prob)) / b
    return min(round(max(0, k), 4), KELLY_CAP)

def analyze():
    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"})
        games = res.json()
    except: return

    all_picks = []
    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        if (utc_time + timedelta(hours=8)).hour < 6: continue

        home_en, away_en = g["home_team"], g["away_team"]
        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        
        m_list = bookmakers[0].get("markets", [])
        h2h = next((m["outcomes"] for m in m_list if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in m_list if m["key"] == "spreads"), None)
        if not h2h: continue

        h_ml = next(o for o in h2h if o["name"] == home_en)["price"]
        a_ml = next(o for o in h2h if o["name"] == away_en)["price"]
        p_home = min((1/h_ml) / ((1/h_ml) + (1/a_ml)) + 0.03, 0.96)
        p_away = 1 - p_home

        game_picks = []
        # (A) 獨贏
        for t_en, prob, odds in [(home_en, p_home, h_ml), (away_en, p_away, a_ml)]:
            edge = prob - (1/odds)
            if edge >= EDGE_THRESHOLD and ODDS_MIN <= odds <= ODDS_MAX:
                game_picks.append({
                    "game": f"{cn(away_en)} @ {cn(home_en)}",
                    "pick": f"獨贏：{cn(t_en)}",
                    "odds": odds, "edge": edge, "kelly": kelly(prob, odds)
                })

        # (B) 讓分盤
        if spreads:
            for o in spreads:
                point, odds = o["point"], o["price"]
                if ODDS_MIN <= odds <= ODDS_MAX:
                    p_spread = 0.5 + ((p_home if o["name"] == home_en else p_away) - 0.5) * SPREAD_COEF
                    edge = p_spread - (1/odds)
                    if edge >= EDGE_THRESHOLD:
                        prefix = "受讓" if point > 0 else "讓分"
                        game_picks.append({
                            "game": f"{cn(away_en)} @ {cn(home_en)}",
                            "pick": f"{prefix}：{cn(o['name'])} ({point:+})",
                            "odds": odds, "edge": edge, "kelly": kelly(p_spread, odds)
                        })

        if game_picks:
            game_picks.sort(key=lambda x: x["edge"], reverse=True)
            all_picks.append(game_picks[0])

    all_picks.sort(key=lambda x: x["edge"], reverse=True)
    final_picks = all_picks[:2]

    msg = f"📊 NBA V15.3 Investor - {datetime.now().strftime('%m/%d %H:%M')}\n---"
    
    total_potential_profit = 0
    if not final_picks:
        msg += "\n今日無符合高價值投資區間之場次。"
    else:
        for r in final_picks:
            potential = r["kelly"] * (r["odds"] - 1)
            total_potential_profit += potential
            icon = "🎯" if r["odds"] >= 2.0 else "✅"
            msg += f"\n🏀 **{r['game']}**\n> {icon} {r['pick']}\n> 賠率：{r['odds']:.2f} | Edge：{r['edge']:.2%}\n> 建議倉位：{r['kelly']:.2%}\n"

        msg += f"\n---"
        msg += f"\n💰 **今日目標收益率：+{total_potential_profit:.2%}**"
        msg += f"\n*(此數據為兩場皆過對總資金的預期貢獻)*"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    analyze()
