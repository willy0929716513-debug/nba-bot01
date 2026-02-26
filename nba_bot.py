import requests
import os
import json
from datetime import datetime, timedelta

# ===== V15.1 Sentinel 參數 =====
EDGE_THRESHOLD = 0.025
KELLY_CAP = 0.05
SPREAD_COEF = 0.18
HOME_BOOST = 0.03
STRENGTH_FILE = "team_strength.json"

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

def get_rolling_strength(current_games):
    """更新並回傳滾動實力分"""
    try:
        data = {}
        if os.path.exists(STRENGTH_FILE):
            with open(STRENGTH_FILE, 'r') as f: data = json.load(f)
        
        for g in current_games:
            bookmakers = g.get("bookmakers", [])
            if not bookmakers: continue
            h2h = next((m["outcomes"] for m in bookmakers[0]["markets"] if m["key"] == "h2h"), None)
            if h2h:
                for o in h2h:
                    team, score = o["name"], round(1/o["price"], 3)
                    data[team] = data.get(team, []) + [score]
                    data[team] = data[team][-15:] # 擴大追蹤至 15 場

        with open(STRENGTH_FILE, 'w') as f: json.dump(data, f)
        return {t: sum(s)/len(s) for t, s in data.items()}
    except: return {}

def kelly(prob, odds):
    b = odds - 1
    if prob <= 1/odds: return 0
    return min(round(max(0, (prob * b - (1 - prob)) / b), 4), KELLY_CAP)

def analyze():
    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"})
        games = res.json()
    except: return

    powers = get_rolling_strength(games)
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

        # 基礎勝率 + 實力修正
        h_pow, a_pow = powers.get(home_en, 0.5), powers.get(away_en, 0.5)
        p_home_base = (1/h_ml) / ((1/h_ml) + (1/a_ml))
        p_home = min(p_home_base + HOME_BOOST + (h_pow - a_pow)*0.08, 0.96)
        p_away = 1 - p_home

        game_picks = []
        # (A) 獨贏
        for t_en, prob, odds in [(home_en, p_home, h_ml), (away_en, p_away, a_ml)]:
            edge = prob - (1/odds)
            if edge >= EDGE_THRESHOLD:
                # 判定是否倒打
                opp_pow = a_pow if t_en == home_en else h_pow
                my_pow = h_pow if t_en == home_en else a_pow
                tag = "⚠️ 倒打預警" if my_pow < opp_pow - 0.15 else "🛡️ 實力穩定"
                game_picks.append({"game":f"{cn(away_en)} @ {cn(home_en)}","pick":f"獨贏：{cn(t_en)}","edge":edge,"kelly":kelly(prob, odds), "tag": tag})

        # (B) 讓分 (邏輯同 V14) ... 此處略以節省篇幅
        
        if game_picks:
            game_picks.sort(key=lambda x: x["edge"], reverse=True)
            all_picks.append(game_picks[0])

    all_picks.sort(key=lambda x: x["edge"], reverse=True)
    msg = f"🛰️ NBA V15.1 Sentinel - {datetime.now().strftime('%m/%d %H:%M')}\n---"
    
    if not all_picks:
        msg += "\n今日掃描完成，無符合優勢場次。"
    else:
        for r in all_picks[:2]:
            icon = "💎" if r["edge"] >= 0.04 else "✅"
            msg += f"\n🏀 **{r['game']}**\n> {icon} {r['pick']}\n> {r['tag']}\n> Edge：{r['edge']:.2%}\n> 倉位：{r['kelly']:.2%}\n"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    analyze()
