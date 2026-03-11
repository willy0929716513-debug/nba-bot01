import requests
import os
import json
import random
import math
from datetime import datetime, timedelta

# ==========================================
# NBA V79.2 Quantum Shield - Fix Edition
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
DB_FILE = "nba_v79_db.json"

SIMS = 25000 
MIN_EV_THRESHOLD = 0.3  
HOME_ADV = 2.4
SPREAD_STD = 14.2 
DISPLAY_TIME_OFFSET = -10 

TEAM_CN = {"Boston Celtics":"塞爾提克","Milwaukee Bucks":"公鹿","Denver Nuggets":"金塊","Golden State Warriors":"勇士","Los Angeles Lakers":"湖人","Phoenix Suns":"太陽","Dallas Mavericks":"獨行俠","Los Angeles Clippers":"快艇","Miami Heat":"熱火","Philadelphia 76ers":"七六人","New York Knicks":"尼克","Toronto Raptors":"暴龍","Chicago Bulls":"公牛","Atlanta Hawks":"老鷹","Brooklyn Nets":"籃網","Cleveland Cavaliers":"騎士","Indiana Pacers":"溜馬","Detroit Pistons":"活塞","Orlando Magic":"魔術","Charlotte Hornets":"黃蜂","Washington Wizards":"巫師","Houston Rockets":"火箭","San Antonio Spurs":"馬刺","Memphis Grizzlies":"灰熊","New Orleans Pelicans":"鵜鶘","Minnesota Timberwolves":"灰狼","Oklahoma City Thunder":"雷霆","Utah Jazz":"爵士","Portland Trail Blazers":"拓荒者","Sacramento Kings":"國王"}

TEAM_STATS = {
    "Boston Celtics": {"off":121,"def":110}, "Denver Nuggets": {"off":118,"def":112},
    "Oklahoma City Thunder": {"off":120,"def":111}, "Milwaukee Bucks": {"off":119,"def":113},
    "Minnesota Timberwolves": {"off":115,"def":108}, "Los Angeles Clippers": {"off":117,"def":112},
    "Dallas Mavericks": {"off":119,"def":114}, "Phoenix Suns": {"off":117,"def":113},
    "Golden State Warriors": {"off":118,"def":114}, "Los Angeles Lakers": {"off":116,"def":114},
    "New York Knicks": {"off":116,"def":111}, "Cleveland Cavaliers": {"off":115,"def":110},
    "Philadelphia 76ers": {"off":118,"def":113}, "Sacramento Kings": {"off":119,"def":115},
    "Miami Heat": {"off":112,"def":111}, "Indiana Pacers": {"off":122,"def":118},
    "Houston Rockets": {"off":114,"def":111}, "New Orleans Pelicans": {"off":116,"def":111},
    "Atlanta Hawks": {"off":118,"def":118}, "Chicago Bulls": {"off":111,"def":113},
    "Toronto Raptors": {"off":110,"def":115}, "Brooklyn Nets": {"off":111,"def":114},
    "Charlotte Hornets": {"off":108,"def":118}, "Detroit Pistons": {"off":109,"def":119},
    "Utah Jazz": {"off":112,"def":118}, "Portland Trail Blazers": {"off":108,"def":118},
    "San Antonio Spurs": {"off":112,"def":119}, "Washington Wizards": {"off":109,"def":120},
    "Memphis Grizzlies": {"off":113,"def":113}, "Orlando Magic": {"off":113,"def":110}
}

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: return json.load(f)
        except: pass
    return {"history": {}, "locks": {}}

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f)

def predict_spread(home, away):
    h = TEAM_STATS.get(home, {"off":112,"def":112})
    a = TEAM_STATS.get(away, {"off":112,"def":112})
    return (h["off"] - a["def"]) - (a["off"] - h["def"]) + HOME_ADV

def run():
    db = load_db()
    now_tw = datetime.utcnow() + timedelta(hours=8)
    
    try:
        r = requests.get(f"{BASE_URL}/odds/", params={"apiKey":API_KEY, "regions":"us", "markets":"spreads,h2h", "oddsFormat":"decimal"})
        r.raise_for_status()
        games = r.json()
    except Exception as e:
        print(f"API Error: {e}")
        return
    
    grouped = {}

    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        if abs((commence_tw - now_tw).total_seconds()) > 86400: continue
        
        display_tw = commence_tw + timedelta(minutes=DISPLAY_TIME_OFFSET)
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")

        books = g.get("bookmakers", [])
        if not books: continue
        
        best_pick = None
        for b_obj in books:
            for m_obj in b_obj["markets"]:
                for o in m_obj["outcomes"]:
                    model_val = predict_spread(home, away)
                    line = o.get("point", 0)
                    
                    win_hits = 0
                    expected_margin = model_val if o["name"]==home else -model_val
                    for _ in range(SIMS):
                        if (expected_margin + random.gauss(0, SPREAD_STD) + line) > 0:
                            win_hits += 1
                    
                    prob = win_hits / SIMS
                    ev = (prob * (o["price"] - 1)) - (1 - prob)
                    
                    if ev < MIN_EV_THRESHOLD: continue
                    
                    label = "[受讓]" if line > 0 else "[讓分]"
                    if m_obj["key"] == "h2h": label = "[獨贏]"

                    pick = {
                        "team": o["name"], "point": line, "odds": o["price"],
                        "prob": prob, "implied": 1/o["price"], "ev": ev, 
                        "label": label, "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                        "display_time": display_tw.strftime("%H:%M")
                    }
                    if best_pick is None or ev > best_pick["ev"]: best_pick = pick

        if best_pick:
            grouped.setdefault(date_key, []).append(best_pick)

    message = f"🛡️ **NBA V79.2 Quantum Shield**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    
    if not grouped:
        message += "\n📭 目前無符合高 EV 要求的場次。"
    else:
        for date, picks in grouped.items():
            message += f"\n📅 **{date}**\n"
            picks.sort(key=lambda x: x["ev"], reverse=True)
            for p in picks:
                # 修正處：將點數格式化邏輯分開處理，避免空字串報錯
                point_str = f"{p['point']:+}" if p['point'] != 0 else ""
                
                message += f"**{p['match']}** {p['label']}\n"
                message += f"✨ ⏰ {p['display_time']} | 🎯 {TEAM_CN.get(p['team'],p['team'])} {point_str} @ **{p['odds']}**\n"
                message += f"💰 EV: **+{p['ev']:.2f}** | 📊 勝率: **{p['prob']:.1%}**\n"
                message += "--------\n"

    requests.post(WEBHOOK, json={"content": message})
    save_db(db)

if __name__=="__main__":
    run()
