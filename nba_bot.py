import requests
import os
import json
import random
from datetime import datetime, timedelta

# ===============================
# NBA V61.3 SENTINEL PRO (Smart Sorting & Risk Filter)
# ===============================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
DB_FILE = "nba_v61_db.json"

SIMS = 15000
EDGE_THRESHOLD = 0.025
HOME_ADV = 2.5
K_FACTOR = 22
CLV_ALERT_THRESHOLD = -0.05

INITIAL_ELO = {
    "Boston Celtics": 1680, "Denver Nuggets": 1655, "Oklahoma City Thunder": 1640,
    "Milwaukee Bucks": 1625, "Minnesota Timberwolves": 1610, "Los Angeles Clippers": 1590,
    "Dallas Mavericks": 1585, "Phoenix Suns": 1580, "Golden State Warriors": 1575,
    "Los Angeles Lakers": 1565, "New York Knicks": 1560, "Cleveland Cavaliers": 1555,
    "Philadelphia 76ers": 1545, "Sacramento Kings": 1535, "Miami Heat": 1520,
    "Indiana Pacers": 1510, "Houston Rockets": 1505, "New Orleans Pelicans": 1500,
    "Atlanta Hawks": 1495, "Chicago Bulls": 1490, "Toronto Raptors": 1480,
    "Brooklyn Nets": 1470, "Charlotte Hornets": 1460, "Detroit Pistons": 1450,
    "Utah Jazz": 1445, "Portland Trail Blazers": 1440, "San Antonio Spurs": 1435,
    "Washington Wizards": 1425, "Memphis Grizzlies": 1500, "Orlando Magic": 1515
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
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: return json.load(f)
        except: pass
    return {"elo": INITIAL_ELO, "processed_games": [], "history": {}}

def save_db(db):
    with open(DB_FILE, "w") as f: json.dump(db, f, indent=2)

def update_elo_with_garbage_filter(db):
    try:
        res = requests.get(f"{BASE_URL}/scores/", params={"apiKey": API_KEY, "daysFrom": 2}, timeout=15)
        scores = res.json()
    except: return
    elo = db.get("elo", INITIAL_ELO)
    processed = db.get("processed_games", [])
    for s in scores:
        gid = s["id"]
        if gid in processed or not s.get("completed"): continue
        home_team, away_team = s["home_team"], s["away_team"]
        h_score = next((int(it["score"]) for it in s["scores"] if it["name"] == home_team), 0)
        a_score = next((int(it["score"]) for it in s["scores"] if it["name"] == away_team), 0)
        dr = elo.get(home_team, 1500) - elo.get(away_team, 1500) + 100
        expected_h = 1 / (10 ** (-dr / 400) + 1)
        actual_h = 1 if h_score > a_score else 0
        mov = abs(h_score - a_score)
        adj_mov = 20 + (mov - 20) ** 0.5 if mov > 20 else mov
        multiplier = ((adj_mov + 3) ** 0.8) / (7.5 + 0.006 * dr)
        shift = K_FACTOR * multiplier * (actual_h - expected_h)
        elo[home_team] = round(elo.get(home_team, 1500) + shift, 2)
        elo[away_team] = round(elo.get(away_team, 1500) - shift, 2)
        processed.append(gid)
    db["elo"] = elo
    db["processed_games"] = processed[-100:]

def run():
    db = load_db()
    update_elo_with_garbage_filter(db)
    elo, history = db["elo"], db["history"]
    now_tw = datetime.utcnow() + timedelta(hours=8)

    try:
        res = requests.get(f"{BASE_URL}/odds/", params={"apiKey": API_KEY, "regions": "us", "markets": "spreads", "oddsFormat": "decimal"}, timeout=15)
        games = res.json()
    except: return

    grouped_results = {}

    for g in games:
        home, away, gid = g["home_team"], g["away_team"], g["id"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")
        is_live = now_tw > (commence_tw - timedelta(minutes=2))
        
        books = g.get("bookmakers", [])
        if not books: continue
        market = next((m["outcomes"] for m in books[0]["markets"] if m["key"] == "spreads"), None)
        if not market: continue

        best_pick = None
        for o in market:
            if gid not in history:
                history[gid] = {"open": o["price"], "team": o["name"]}
            
            clv = (o["price"] - history[gid]["open"]) / history[gid]["open"]
            is_injury = clv < CLV_ALERT_THRESHOLD
            model_line = ((elo.get(home, 1500) - elo.get(away, 1500)) / 28) + HOME_ADV
            if is_injury: model_line -= 4.5 if o["name"] == home else -4.5
            
            prob = sum(1 for _ in range(SIMS) if random.gauss(model_line, 13.5) + (o["point"] if o["name"] == home else -o["point"]) > 0) / SIMS
            edge = prob - (1/o["price"])
            
            if best_pick is None or edge > best_pick["edge"]:
                best_pick = {"team": o["name"], "point": o["point"], "odds": o["price"], "prob": prob, "edge": edge, "clv": clv, "injury": is_injury, "is_live": is_live, "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}"}

        if best_pick and best_pick["edge"] > EDGE_THRESHOLD:
            if date_key not in grouped_results: grouped_results[date_key] = []
            grouped_results[date_key].append(best_pick)

    message = f"🛡️ **NBA V61.3 Sentinel Pro**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    
    if not grouped_results:
        message += "\n📭 目前無價值偏差場次。"
    else:
        for date, picks in grouped_results.items():
            message += f"\n📅 **{date}**\n"
            
            # --- 智能排序邏輯 ---
            # 優先級 1: CLV > 0 (正向移動) 排前面
            # 優先級 2: Edge 高的排前面
            picks.sort(key=lambda x: (x["clv"] > 0, x["edge"]), reverse=True)
            
            for p in picks:
                implied = 1 / p["odds"]
                k = ( (p["odds"]-1)*p["prob"] - (1-p["prob"]) ) / (p["odds"]-1)
                stake = min(max(0, k * (0.05 if p["injury"] else 0.25)), 0.03)
                
                # 視覺化趨勢
                clv_icon = "📈" if p["clv"] > 0 else ("📉" if p["clv"] < 0 else "↔️")
                status = "⚠️ 傷病" if p["injury"] else ("🔥 S級" if p["edge"] > 0.05 else "⭐ A級")
                live_tag = "🔴 [場中] " if p["is_live"] else ""
                
                message += f"{live_tag}{status} **{p['match']}**\n"
                message += f"🎯 {TEAM_CN.get(p['team'],p['team'])} {p['point']:+} @ **{p['odds']}**\n"
                message += f"Edge: **{p['edge']:.2%}** | CLV: {clv_icon} **{p['clv']:.2%}**\n"
                message += f"勝率: 模型 **{p['prob']:.1%}** (市場 {implied:.1%})\n"
                message += f"建議注量: **{stake:.2%}**\n"
                message += "--------\n"

    requests.post(WEBHOOK, json={"content": message})
    save_db({"elo": elo, "processed_games": db["processed_games"], "history": history})

if __name__ == "__main__":
    run()
