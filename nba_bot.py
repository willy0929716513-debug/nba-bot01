import requests
import os
import json
import random
from datetime import datetime, timedelta

# ==========================================
# NBA V71 Stable Quantum AI (Fixed & Grouped)
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
DB_FILE = "nba_v71_db.json"

SIMS = 20000
EDGE_THRESHOLD = 0.03
LOCK_THRESHOLD = 0.015 # 鎖定緩衝，防止數據跳動
HOME_ADV = 2.4
SPREAD_STD = 13.5

TEAM_CN = {
    "Boston Celtics":"塞爾提克", "Milwaukee Bucks":"公鹿", "Denver Nuggets":"金塊",
    "Golden State Warriors":"勇士", "Los Angeles Lakers":"湖人", "Phoenix Suns":"太陽",
    "Dallas Mavericks":"獨行俠", "Los Angeles Clippers":"快艇", "Miami Heat":"熱火",
    "Philadelphia 76ers":"七六人", "New York Knicks":"尼克", "Toronto Raptors":"暴龍",
    "Chicago Bulls":"公牛", "Atlanta Hawks":"老鷹", "Brooklyn Nets":"籃網",
    "Cleveland Cavaliers":"騎士", "Indiana Pacers":"溜馬", "Detroit Pistons":"活塞",
    "Orlando Magic":"魔術", "Charlotte Hornets":"黃蜂", "Washington Wizards":"巫師",
    "Houston Rockets":"火箭", "San Antonio Spurs":"馬刺", "Memphis Grizzlies":"灰熊",
    "New Orleans Pelicans":"鵜鶘", "Minnesota Timberwolves":"灰狼", "Oklahoma City Thunder":"雷霆",
    "Utah Jazz":"爵士", "Sacramento Kings":"國王", "Portland Trail Blazers":"拓荒者"
}

TEAM_STATS = {
    "Boston Celtics": {"off":121,"def":110,"pace":99},
    "Denver Nuggets": {"off":118,"def":112,"pace":97},
    "Oklahoma City Thunder": {"off":120,"def":111,"pace":101},
    "Milwaukee Bucks": {"off":119,"def":113,"pace":100},
    "Minnesota Timberwolves": {"off":115,"def":108,"pace":97},
    "Los Angeles Clippers": {"off":117,"def":112,"pace":98},
    "Dallas Mavericks": {"off":119,"def":114,"pace":100},
    "Phoenix Suns": {"off":117,"def":113,"pace":99},
    "Golden State Warriors": {"off":118,"def":114,"pace":101},
    "Los Angeles Lakers": {"off":116,"def":114,"pace":101},
    "New York Knicks": {"off":116,"def":111,"pace":95},
    "Cleveland Cavaliers": {"off":115,"def":110,"pace":96},
    "Philadelphia 76ers": {"off":118,"def":113,"pace":97},
    "Sacramento Kings": {"off":119,"def":115,"pace":101},
    "Miami Heat": {"off":112,"def":111,"pace":95},
    "Indiana Pacers": {"off":122,"def":118,"pace":103},
    "Houston Rockets": {"off":114,"def":111,"pace":98},
    "New Orleans Pelicans": {"off":116,"def":111,"pace":98},
    "Atlanta Hawks": {"off":118,"def":118,"pace":102},
    "Chicago Bulls": {"off":111,"def":113,"pace":97},
    "Toronto Raptors": {"off":110,"def":115,"pace":100},
    "Brooklyn Nets": {"off":111,"def":114,"pace":98},
    "Charlotte Hornets": {"off":108,"def":118,"pace":101},
    "Detroit Pistons": {"off":109,"def":119,"pace":100},
    "Utah Jazz": {"off":112,"def":118,"pace":101},
    "Portland Trail Blazers": {"off":108,"def":118,"pace":99},
    "San Antonio Spurs": {"off":112,"def":119,"pace":101},
    "Washington Wizards": {"off":109,"def":120,"pace":102},
    "Memphis Grizzlies": {"off":113,"def":113,"pace":100},
    "Orlando Magic": {"off":113,"def":110,"pace":97}
}

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE,"r") as f: return json.load(f)
    return {"locks": {}}

def save_db(db):
    with open(DB_FILE,"w") as f: json.dump(db, f, indent=2)

def predict_spread(home, away):
    h, a = TEAM_STATS.get(home, {"off":115,"def":115}), TEAM_STATS.get(away, {"off":115,"def":115})
    rating_h, rating_a = h["off"]-h["def"], a["off"]-a["def"]
    return (rating_h - rating_a) / 2 + HOME_ADV

def simulate_spread(model, line):
    win = sum(1 for _ in range(SIMS) if random.gauss(model, SPREAD_STD) + line > 0)
    return win / SIMS

def kelly(prob, odds):
    b = odds - 1
    k = ((b * prob) - (1 - prob)) / b
    return max(0, min(k * 0.25, 0.03))

def run():
    db = load_db()
    locks = db.get("locks", {})
    now_tw = datetime.utcnow() + timedelta(hours=8)
    
    r = requests.get(f"{BASE_URL}/odds/", params={"apiKey":API_KEY, "regions":"us", "markets":"spreads", "oddsFormat":"decimal"})
    games = r.json()
    
    grouped = {}
    
    for g in games:
        gid = g["id"]
        home, away = g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")
        
        books = g.get("bookmakers", [])
        if not books: continue
        market = next((m["outcomes"] for m in books[0]["markets"] if m["key"]=="spreads"), None)
        if not market: continue

        model_val = predict_spread(home, away)
        best_pick = None

        for o in market:
            prob = simulate_spread(model_val if o["name"]==home else -model_val, o["point"])
            edge = prob - (1/o["price"])
            
            # 鎖定邏輯：如果該場比賽之前已經鎖定了推薦，則降低門檻維持推薦
            is_prev_locked = (gid in locks and locks[gid]["team"] == o["name"])
            threshold = LOCK_THRESHOLD if is_prev_locked else EDGE_THRESHOLD

            if edge > threshold:
                if best_pick is None or edge > best_pick["edge"]:
                    best_pick = {
                        "team": o["name"], "point": o["point"], "odds": o["price"],
                        "prob": prob, "edge": edge, "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                        "time": commence_tw.strftime("%H:%M"), "locked": is_prev_locked
                    }

        if best_pick:
            if date_key not in grouped: grouped[date_key] = []
            grouped[date_key].append(best_pick)
            locks[gid] = {"team": best_pick["team"], "point": best_pick["point"]}

    # Discord 輸出
    message = f"🛰️ **NBA V71 Stable Quantum**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    if not grouped:
        message += "\n📭 今日無優勢投注"
    else:
        for date, picks in grouped.items():
            message += f"\n📅 **{date}**\n"
            picks.sort(key=lambda x: x["edge"], reverse=True)
            for p in picks:
                icon = "🔒" if p["locked"] else "✨"
                message += f"{icon} **{p['match']}**\n"
                message += f"⏰ {p['time']} | 🎯 {TEAM_CN.get(p['team'],p['team'])} {p['point']:+} @ **{p['odds']}**\n"
                message += f"Edge: **{p['edge']:.2%}** | Stake: **{kelly(p['prob'], p['odds']):.2%}**\n"
                message += "--------\n"

    requests.post(WEBHOOK, json={"content": message})
    save_db({"locks": locks})

if __name__=="__main__":
    run()
