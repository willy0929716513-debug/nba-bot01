import requests
import os
import json
import random
from datetime import datetime

# ===== NBA V25.0 Pro Betting Engine =====
STRICT_EDGE_BASE = 0.022
A_GRADE_THRESHOLD = 0.038

SPREAD_COEF = 0.16
DEEP_SPREAD_COEF = 0.13
MONTE_CARLO_RUNS = 1000

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
DB_PATH = "nba_market_data.json"

# 中文隊名
TEAM_CN = {
    "Los Angeles Lakers": "湖人", "Golden State Warriors": "勇士", "Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿", "Denver Nuggets": "金塊", "Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽", "LA Clippers": "快艇", "Miami Heat": "熱火",
    "Philadelphia 76ers": "七六人", "Sacramento Kings": "國王", "New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼", "Dallas Mavericks": "獨行俠", "New York Knicks": "尼克",
    "Orlando Magic": "魔術", "Charlotte Hornets": "黃蜂", "Detroit Pistons": "活塞",
    "Toronto Raptors": "暴龍", "Chicago Bulls": "公牛", "San Antonio Spurs": "馬刺",
    "Utah Jazz": "爵士", "Brooklyn Nets": "籃網", "Atlanta Hawks": "老鷹",
    "Cleveland Cavaliers": "騎士", "Indiana Pacers": "溜馬", "Memphis Grizzlies": "灰熊",
    "Portland Trail Blazers": "拓荒者", "Washington Wizards": "巫師", "Houston Rockets": "火箭"
}

# 球隊真實戰力示例（NetRating / Pace / Back-to-Back / Injury）
TEAM_POWER = {
    "Miami Heat": {"Net": 2.1, "Pace": 100.5, "B2B": False, "Injured": False, "Def": True},
    "Orlando Magic": {"Net": 3.5, "Pace": 102.3, "B2B": False, "Injured": False, "Def": True},
    "Philadelphia 76ers": {"Net": 4.0, "Pace": 98.7, "B2B": True, "Injured": False, "Def": False},
    "LA Clippers": {"Net": 4.8, "Pace": 101.2, "B2B": False, "Injured": True, "Def": False},
    "Boston Celtics": {"Net": 10.2, "Pace": 99.5, "B2B": False, "Injured": False, "Def": True},
    "Denver Nuggets": {"Net": 5.5, "Pace": 103.1, "B2B": True, "Injured": False, "Def": False},
    "Oklahoma City Thunder": {"Net": 7.8, "Pace": 104.0, "B2B": False, "Injured": True, "Def": True},
    "Minnesota Timberwolves": {"Net": 6.2, "Pace": 102.5, "B2B": False, "Injured": False, "Def": True},
    "Milwaukee Bucks": {"Net": 3.8, "Pace": 100.2, "B2B": True, "Injured": False, "Def": False},
    "Phoenix Suns": {"Net": 3.2, "Pace": 101.0, "B2B": False, "Injured": False, "Def": False}
}

# ----------------- DB -----------------
def load_db():
    if os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, 'r') as f:
                return json.load(f)
        except: return {}
    return {}

def save_db(data):
    with open(DB_PATH, 'w') as f:
        json.dump(data, f, indent=4)

# ----------------- Spread 懲罰 -----------------
def spread_penalty(pt, is_home, team_name):
    abs_pt = abs(pt)
    stats = TEAM_POWER.get(team_name, {"Net": 0, "Def": False})
    if abs_pt <= 8: penalty = abs_pt * 0.0028
    elif abs_pt <= 12: penalty = abs_pt * 0.0042
    else: penalty = (abs_pt * 0.0035) ** 1.12
    if not is_home: penalty *= 1.15
    if stats["Def"]: penalty *= 0.85
    return penalty

# ----------------- Kelly 倉位 -----------------
def kelly(prob, odds, edge):
    b = odds - 1
    raw = (prob * b - (1 - prob)) / b
    if edge >= 0.05: cap = 0.035
    elif edge >= 0.038: cap = 0.03
    else: cap = 0.025
    return min(max(0, raw), cap)

# ----------------- 等級 -----------------
def grade(edge):
    if edge >= 0.05: return "🔥 S級"
    elif edge >= A_GRADE_THRESHOLD: return "⭐ A級"
    else: return "✅ 合格"

# ----------------- Monte Carlo 模擬 -----------------
def monte_carlo_sim(team1, team2, pt, is_over):
    t1 = TEAM_POWER.get(team1, {"Net":0,"Pace":100})["Net"]
    t2 = TEAM_POWER.get(team2, {"Net":0,"Pace":100})["Net"]
    wins = 0
    for _ in range(MONTE_CARLO_RUNS):
        score_diff = random.gauss(t1 - t2, 12)  # 標準差假設12
        if is_over and score_diff + pt > 0: wins +=1
        elif not is_over and score_diff + pt < 0: wins +=1
    return wins / MONTE_CARLO_RUNS

# ----------------- 主程式 -----------------
def main():
    db = load_db()
    try:
        res = requests.get(BASE_URL, params={
            "apiKey": API_KEY, "regions":"us","markets":"h2h,spreads,totals","oddsFormat":"decimal"
        }, timeout=15)
        games = res.json()
    except:
        return

    results = {}
    for g in games:
        game_id = g["id"]
        home, away = g["home_team"], g["away_team"]
        game_key = f"{TEAM_CN.get(away, away)} @ {TEAM_CN.get(home, home)}"

        markets = g.get("bookmakers", [{}])[0].get("markets", [])
        h2h = next((m["outcomes"] for m in markets if m["key"]=="h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"]=="spreads"), None)
        totals = next((m["outcomes"] for m in markets if m["key"]=="totals"), None)
        if not h2h: continue

        h_ml = next(o["price"] for o in h2h if o["name"]==home)
        a_ml = next(o["price"] for o in h2h if o["name"]==away)
        p_home = (1/h_ml)/((1/h_ml)+(1/a_ml))

        best_pick = {"edge":-1}
        # Spread
        if spreads:
            for o in spreads:
                pt, odds = o["point"], o["price"]
                is_home = o["name"]==home
                base_p = p_home if is_home else (1-p_home)
                p_spread = 0.5 + (base_p - 0.5)*(DEEP_SPREAD_COEF if abs(pt)>12 else SPREAD_COEF) + (base_p-0.5)*0.06
                penalty = spread_penalty(pt,is_home,o["name"])
                edge = (p_spread - (1/odds))*(1-penalty)
                if edge>best_pick["edge"]:
                    clv = (odds - db.get(game_id, {"open":odds})["open"])/db.get(game_id, {"open":odds})["open"]
                    best_pick = {"pick":f"🎯 {TEAM_CN.get(o['name'],o['name'])} {pt:+}","odds":odds,"edge":edge,"prob":p_spread,"clv":clv}
                    if game_id not in db: db[game_id] = {"open":odds}
        # Totals
        if totals:
            for o in totals:
                pt, odds = o["point"], o["price"]
                is_over = o["name"]=="Over"
                mc_prob = monte_carlo_sim(home, away, pt, is_over)
                edge = (mc_prob - 1/odds)*(1-0.018)
                if edge>best_pick["edge"]:
                    clv = (odds - db.get(game_id, {"open":odds})["open"])/db.get(game_id, {"open":odds})["open"]
                    best_pick = {"pick":f"🏀 {o['name']} {pt}","odds":odds,"edge":edge,"prob":mc_prob,"clv":clv}

        if best_pick["edge"]>=STRICT_EDGE_BASE:
            ev = (best_pick["prob"]*(best_pick["odds"]-1))-(1-best_pick["prob"])
            best_pick["ev"] = ev
            results[game_key] = best_pick

    save_db(db)
    sorted_res = sorted(results.items(), key=lambda x:x[1]["edge"], reverse=True)[:2]

    msg = f"🛰️ **NBA V25.0 Pro Betting Engine**\n{datetime.now().strftime('%m/%d %H:%M')}\n\n"
    if not sorted_res:
        msg += "📭 今日無符合門檻推薦。"
    else:
        for g,p in sorted_res:
            g_grade = grade(p["edge"])
            clv_icon = "📈" if p["clv"]>0 else "📉"
            msg += f"__**{g}**__\n{g_grade} 👉 {p['pick']}\n"
            msg += f"EV: **+{p['ev']:.2%}** | Edge: {p['edge']:.2%} | CLV: {clv_icon}{p['clv']:.2%}\n"
            msg += "---------\n"

    requests.post(WEBHOOK_URL,json={"content":msg})

if __name__=="__main__":
    main()