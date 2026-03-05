import requests
import os
import json
import random
from datetime import datetime

# ===== NBA V28.0 Final Stability (數據校準版) =====
STRICT_EDGE_BASE = 0.022
A_GRADE_THRESHOLD = 0.038
MONTE_CARLO_RUNS = 3000 # 再次增加樣本數以求穩定

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
DB_PATH = "nba_market_data.json"

INITIAL_POWER = {
    "Boston Celtics": {"Net": 10.5, "Def": True}, "Cleveland Cavaliers": {"Net": 8.2, "Def": True},
    "New York Knicks": {"Net": 4.5, "Def": True}, "Milwaukee Bucks": {"Net": 2.8, "Def": False},
    "Orlando Magic": {"Net": 3.8, "Def": True}, "Indiana Pacers": {"Net": -0.5, "Def": False},
    "Philadelphia 76ers": {"Net": 1.2, "Def": False}, "Miami Heat": {"Net": 1.5, "Def": True},
    "Atlanta Hawks": {"Net": -1.8, "Def": False}, "Brooklyn Nets": {"Net": -3.5, "Def": False},
    "Toronto Raptors": {"Net": -5.2, "Def": False}, "Charlotte Hornets": {"Net": -6.8, "Def": False},
    "Chicago Bulls": {"Net": -4.2, "Def": False}, "Washington Wizards": {"Net": -9.5, "Def": False},
    "Detroit Pistons": {"Net": -3.0, "Def": True}, "Oklahoma City Thunder": {"Net": 9.2, "Def": True},
    "Minnesota Timberwolves": {"Net": 5.8, "Def": True}, "Denver Nuggets": {"Net": 5.2, "Def": False},
    "LA Clippers": {"Net": 3.5, "Def": True}, "Dallas Mavericks": {"Net": 4.8, "Def": False},
    "Phoenix Suns": {"Net": 3.0, "Def": False}, "Golden State Warriors": {"Net": 6.5, "Def": True},
    "Sacramento Kings": {"Net": 1.5, "Def": False}, "Los Angeles Lakers": {"Net": 0.8, "Def": False},
    "Houston Rockets": {"Net": 5.5, "Def": True}, "Memphis Grizzlies": {"Net": 3.2, "Def": True},
    "New Orleans Pelicans": {"Net": -2.5, "Def": False}, "San Antonio Spurs": {"Net": -1.5, "Def": True},
    "Utah Jazz": {"Net": -7.5, "Def": False}, "Portland Trail Blazers": {"Net": -8.0, "Def": False}
}

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

def load_db():
    default_data = {"history": {}, "team_power": INITIAL_POWER}
    if os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, 'r') as f:
                data = json.load(f)
                if "history" not in data: data["history"] = {}
                if "team_power" not in data or not data["team_power"]: data["team_power"] = INITIAL_POWER
                return data
        except: return default_data
    return default_data

def save_db(db):
    with open(DB_PATH, 'w') as f:
        json.dump(db, f, indent=4)

def mc_simulate_spread(home_team, away_team, pt, is_home_pick, team_power):
    h_net = team_power.get(home_team, INITIAL_POWER.get(home_team, {"Net": 0}))["Net"]
    a_net = team_power.get(away_team, INITIAL_POWER.get(away_team, {"Net": 0}))["Net"]
    expected_diff = h_net - a_net + 2.5 # 微調主場優勢
    wins = sum(1 for _ in range(MONTE_CARLO_RUNS) if (random.gauss(expected_diff, 13.5) + pt > 0 if is_home_pick else random.gauss(expected_diff, 13.5) + pt < 0))
    return wins / MONTE_CARLO_RUNS

def mc_simulate_totals(line, home_team, away_team, is_over, team_power):
    # 改進：參考市場盤口 (Line) 進行效率微調，不再自創基準
    h_net = team_power.get(home_team, INITIAL_POWER.get(home_team, {"Net": 0}))["Net"]
    a_net = team_power.get(away_team, INITIAL_POWER.get(away_team, {"Net": 0}))["Net"]
    
    # 邏輯：防守爛(Net低)的球隊通常 Pace 快，微幅修正預期總分
    adjustment = (h_net + a_net) * -0.15 
    expected_total = line + adjustment
    
    # NBA 總分標準差通常在 20-24 之間
    wins = sum(1 for _ in range(MONTE_CARLO_RUNS) if (random.gauss(expected_total, 22.5) > line if is_over else random.gauss(expected_total, 22.5) < line))
    return wins / MONTE_CARLO_RUNS

def main():
    db_data = load_db()
    history, team_power = db_data["history"], db_data["team_power"]

    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions":"us","markets":"h2h,spreads,totals","oddsFormat":"decimal"}, timeout=15)
        games = res.json()
    except: return

    results = {}
    for g in games:
        game_id, home, away = g["id"], g["home_team"], g["away_team"]
        markets = g.get("bookmakers", [{}])[0].get("markets", [])
        spreads = next((m["outcomes"] for m in markets if m["key"]=="spreads"), None)
        totals = next((m["outcomes"] for m in markets if m["key"]=="totals"), None)

        best_pick = {"edge": -1}

        # 讓分盤
        if spreads:
            for o in spreads:
                pt, odds = o["point"], o["price"]
                is_home_pick = (o["name"] == home)
                prob = mc_simulate_spread(home, away, pt, is_home_pick, team_power)
                
                if game_id not in history: history[game_id] = {"open": odds, "team": o["name"]}
                clv = (odds - history[game_id]["open"]) / history[game_id]["open"]
                
                # 自動戰力修正 (EMA)
                if abs(clv) > 0.01:
                    t_name = o["name"]
                    if t_name in team_power:
                        adj = 0.04 if clv > 0 else -0.04
                        team_power[t_name]["Net"] = round(team_power[t_name]["Net"] + adj, 3)

                penalty = (abs(pt) * 0.0032)
                edge = (prob - 1/odds) * (1 - penalty)
                if edge > best_pick["edge"]:
                    best_pick = {"pick": f"🎯 {TEAM_CN.get(o['name'], o['name'])} {pt:+}", "odds": odds, "edge": edge, "prob": prob, "clv": clv}

        # 大小分盤 (防止大小分過度自信)
        if totals:
            for o in totals:
                line, odds = o["point"], o["price"]
                prob = mc_simulate_totals(line, home, away, o["name"]=="Over", team_power)
                edge = (prob - 1/odds) * 0.98 # 扣除固定水錢
                
                if edge > best_pick["edge"]:
                    clv = (odds - history.get(game_id, {"open": odds})["open"]) / history.get(game_id, {"open": odds})["open"]
                    best_pick = {"pick": f"🏀 {o['name']} {line}", "odds": odds, "edge": edge, "prob": prob, "clv": clv}

        # --- 數據斷路器：壓縮極端 Edge ---
        if best_pick["edge"] > 0.08:
            best_pick["edge"] = 0.06 + (best_pick["edge"] * 0.15) 

        if best_pick["edge"] >= STRICT_EDGE_BASE:
            best_pick["ev"] = (best_pick["prob"] * (best_pick["odds"] - 1)) - (1 - best_pick["prob"])
            results[f"{TEAM_CN.get(away, away)} @ {TEAM_CN.get(home, home)}"] = best_pick

    db_data["history"], db_data["team_power"] = history, team_power
    save_db(db_data)

    sorted_res = sorted(results.items(), key=lambda x: x[1]["edge"], reverse=True)[:2]
    msg = f"🛰️ **NBA V28.0 Final Stability**\n{datetime.now().strftime('%m/%d %H:%M')}\n\n"
    if not sorted_res:
        msg += "📭 今日無門檻推薦。"
    else:
        for g, p in sorted_res:
            grade_str = "🔥 S級" if p["edge"] >= 0.05 else ("⭐ A級" if p["edge"] >= A_GRADE_THRESHOLD else "✅ 合格")
            msg += f"__**{g}**__\n{grade_str} 👉 {p['pick']}\n"
            msg += f"EV: **+{p['ev']:.2%}** | Edge: {p['edge']:.2%} | CLV: {'📈' if p['clv']>0 else '📉'}{p['clv']:.2%}\n"
            msg += "---------\n"
    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
