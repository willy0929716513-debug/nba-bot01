import requests
import os
import json
import random
from datetime import datetime, timedelta

# ===== NBA V32.0 Context-Aware (日期分組+場中標註版) =====
STRICT_EDGE_BASE = 0.022
A_GRADE_THRESHOLD = 0.038
MONTE_CARLO_RUNS = 3000
CLV_DANGER_ZONE = -0.05

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
DB_PATH = "nba_market_data.json"

# [戰力字典 INITIAL_POWER 與 隊名 TEAM_CN 維持不變，略過以節省篇幅]
INITIAL_POWER = {
    "Boston Celtics": {"Net": 10.5}, "Cleveland Cavaliers": {"Net": 8.2},
    "New York Knicks": {"Net": 4.5}, "Milwaukee Bucks": {"Net": 2.8},
    "Orlando Magic": {"Net": 3.8}, "Indiana Pacers": {"Net": -0.5},
    "Philadelphia 76ers": {"Net": 1.2}, "Miami Heat": {"Net": 1.5},
    "Atlanta Hawks": {"Net": -1.8}, "Brooklyn Nets": {"Net": -3.5},
    "Toronto Raptors": {"Net": -5.2}, "Charlotte Hornets": {"Net": -6.8},
    "Chicago Bulls": {"Net": -4.2}, "Washington Wizards": {"Net": -9.5},
    "Detroit Pistons": {"Net": -3.0}, "Oklahoma City Thunder": {"Net": 9.2},
    "Minnesota Timberwolves": {"Net": 5.8}, "Denver Nuggets": {"Net": 5.2},
    "Los Angeles Clippers": {"Net": 3.5}, "Dallas Mavericks": {"Net": 4.8},
    "Phoenix Suns": {"Net": 3.0}, "Golden State Warriors": {"Net": 6.5},
    "Sacramento Kings": {"Net": 1.5}, "Los Angeles Lakers": {"Net": 0.8},
    "Houston Rockets": {"Net": 5.5}, "Memphis Grizzlies": {"Net": 3.2},
    "New Orleans Pelicans": {"Net": -2.5}, "San Antonio Spurs": {"Net": -1.5},
    "Utah Jazz": {"Net": -7.5}, "Portland Trail Blazers": {"Net": -8.0}
}

TEAM_CN = {
    "Los Angeles Lakers": "湖人", "Golden State Warriors": "勇士", "Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿", "Denver Nuggets": "金塊", "Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽", "Los Angeles Clippers": "快艇", "Miami Heat": "熱火",
    "Philadelphia 76ers": "七六人", "Sacramento Kings": "國王", "New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼", "Dallas Mavericks": "獨行俠", "New York Knicks": "尼克",
    "Orlando Magic": "魔術", "Charlotte Hornets": "黃蜂", "Detroit Pistons": "活塞",
    "Toronto Raptors": "暴龍", "Chicago Bulls": "公牛", "San Antonio Spurs": "馬刺",
    "Utah Jazz": "爵士", "Brooklyn Nets": "籃網", "Atlanta Hawks": "老鷹",
    "Cleveland Cavaliers": "騎士", "Indiana Pacers": "溜馬", "Memphis Grizzlies": "灰熊",
    "Portland Trail Blazers": "拓荒者", "Washington Wizards": "巫師", "Houston Rockets": "火箭"
}

def load_db():
    if os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, 'r') as f: return json.load(f)
        except: pass
    return {"history": {}, "team_power": INITIAL_POWER}

def save_db(db):
    with open(DB_PATH, 'w') as f: json.dump(db, f, indent=4)

def calculate_kelly(prob, odds, is_danger=False):
    b = odds - 1
    q = 1 - prob
    kelly_f = (b * prob - q) / b if b != 0 else 0
    mult = 0.005 if is_danger else 0.25
    limit = 0.005 if is_danger else 0.03
    return min(max(0, kelly_f * mult), limit)

def get_net_rating(team_name, team_power):
    return team_power.get(team_name, INITIAL_POWER.get(team_name, {"Net": 0})).get("Net", 0)

def mc_simulate_spread(home_team, away_team, pt, is_home_pick, team_power):
    h_net, a_net = get_net_rating(home_team, team_power), get_net_rating(away_team, team_power)
    expected_diff = h_net - a_net + 2.5
    wins = sum(1 for _ in range(MONTE_CARLO_RUNS) if (random.gauss(expected_diff, 13.5) + pt > 0 if is_home_pick else random.gauss(expected_diff, 13.5) + pt < 0))
    return wins / MONTE_CARLO_RUNS

def main():
    db_data = load_db()
    history, team_power = db_data["history"], db_data["team_power"]
    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions":"us","markets":"h2h,spreads,totals","oddsFormat":"decimal"}, timeout=15)
        games = res.json()
    except: return

    grouped_results = {}
    for g in games:
        # 處理日期與狀態
        commence_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        date_str = commence_time.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")
        
        # 判斷是否為場中 (API 根據時間判定)
        is_live = datetime.now() > (commence_time - timedelta(minutes=5))
        
        game_id, home, away = g["id"], g["home_team"], g["away_team"]
        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        markets = bookmakers[0].get("markets", [])
        spreads = next((m["outcomes"] for m in markets if m["key"]=="spreads"), None)
        
        best_pick = {"edge": -1}
        if spreads:
            for o in spreads:
                pt, odds = o["point"], o["price"]
                prob = mc_simulate_spread(home, away, pt, o["name"]==home, team_power)
                if game_id not in history: history[game_id] = {"open": odds, "team": o["name"]}
                clv = (odds - history[game_id]["open"]) / history[game_id]["open"]
                if abs(clv) > 0.01 and o["name"] in team_power:
                    team_power[o["name"]]["Net"] = round(team_power[o["name"]]["Net"] + (0.04 if clv > 0 else -0.04), 3)
                
                edge = (prob - 1/odds) * (1 - (abs(pt) * 0.0032))
                if edge > best_pick["edge"]:
                    best_pick = {"pick": f"🎯 {TEAM_CN.get(o['name'], o['name'])} {pt:+}", "odds": odds, "edge": edge, "prob": prob, "clv": clv, "live": is_live}

        if best_pick["edge"] >= STRICT_EDGE_BASE:
            best_pick["ev"] = (best_pick["prob"] * (best_pick["odds"] - 1)) - (1 - best_pick["prob"])
            is_danger = best_pick["clv"] <= CLV_DANGER_ZONE
            best_pick["kelly"] = calculate_kelly(best_pick["prob"], best_pick["odds"], is_danger)
            best_pick["danger"] = is_danger
            
            if date_str not in grouped_results: grouped_results[date_str] = []
            grouped_results[date_str].append((f"{TEAM_CN.get(away, away)} @ {TEAM_CN.get(home, home)}", best_pick))

    save_db({"history": history, "team_power": team_power})

    msg = f"🛰️ **NBA V32.0 Smart Grouping**\n偵測時間：{datetime.now().strftime('%m/%d %H:%M')}\n"
    if not grouped_results:
        msg += "\n📭 今日無符合門檻推薦。"
    else:
        for date, matches in grouped_results.items():
            msg += f"\n📅 **{date}**\n"
            for g_name, p in matches:
                live_tag = "🔴 [場中] " if p["live"] else ""
                grade = "⚠️ 數據異常" if p["danger"] else ("🔥 S級" if p["edge"] >= 0.05 else "⭐ A級")
                msg += f"{live_tag}__**{g_name}**__\n{grade} 👉 {p['pick']}\n"
                msg += f"EV: **+{p['ev']:.2%}** | Edge: **{p['edge']:.2%}** | Kelly: **{p['kelly']:.2%}**\n"
                msg += f"CLV: {'📈' if p['clv']>0 else '📉'}{p['clv']:.2%} | Odds: {p['odds']}\n"
                msg += "---------\n"
    
    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
