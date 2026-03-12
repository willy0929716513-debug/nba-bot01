import requests
import os
import json
import random
import math
from datetime import datetime, timedelta

# ==========================================
# NBA V80.2 Quantum Sync - Bankroll Master
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
DB_FILE = "nba_v80_db.json"

# --- 核心參數 ---
TIME_OFFSET_MINUTES = -10  
SIMS = 25000               # 提高模擬次數
EDGE_THRESHOLD = 0.03      
PARLAY_EV_THRESHOLD = 0.05 
HOME_ADV = 2.4             
SPREAD_STD = 13.8          

# 資金管理參數
TOTAL_BANKROLL = 1000      # 總本金 1000 元
KELLY_FRACTION = 0.25      # 1/4 凱利係數 (保守型)
MAX_SINGLE_BET = 150       # 單注最高上限 150 元

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
            with open(DB_FILE,"r") as f: return json.load(f)
        except: pass
    return {"history":{}, "locks": {}}

def save_db(db):
    with open(DB_FILE,"w") as f: json.dump(db, f, indent=2)

def predict_spread(home, away):
    h = TEAM_STATS.get(home, {"off":112,"def":112})
    a = TEAM_STATS.get(away, {"off":112,"def":112})
    return (h["off"] - a["def"]) - (a["off"] - h["def"]) + HOME_ADV

def run():
    db = load_db()
    history, locks = db.get("history", {}), db.get("locks", {})
    now_tw = datetime.utcnow() + timedelta(hours=8)
    
    try:
        r = requests.get(f"{BASE_URL}/odds/", params={"apiKey":API_KEY, "regions":"us", "markets":"spreads", "oddsFormat":"decimal"})
        r.raise_for_status()
        games = r.json()
    except: return
    
    grouped = {}

    for g in games:
        gid, home, away = g["id"], g["home_team"], g["away_team"]
        raw_commence = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        sync_commence = raw_commence + timedelta(minutes=TIME_OFFSET_MINUTES)
        
        if now_tw > (sync_commence + timedelta(minutes=150)): continue
        
        date_key = sync_commence.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")
        
        books = g.get("bookmakers", [])
        if not books: continue
        
        best_pick_in_game = None
        for m_obj in books[0]["markets"]:
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
                edge = prob - (1/o["price"])
                
                if edge > EDGE_THRESHOLD:
                    # 凱利公式計算注碼
                    b_val = o["price"] - 1
                    k_p = (b_val * prob - (1 - prob)) / b_val
                    suggested_bet = round(max(0, min(TOTAL_BANKROLL * k_p * KELLY_FRACTION, MAX_SINGLE_BET)))
                    
                    pick = {
                        "team": o["name"], "point": line, "odds": o["price"],
                        "prob": prob, "ev": ev, "edge": edge, "bet": suggested_bet,
                        "label": "[受讓]" if line > 0 else "[讓分]",
                        "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                        "time": sync_commence.strftime("%H:%M")
                    }
                    if best_pick_in_game is None or ev > best_pick_in_game["ev"]:
                        best_pick_in_game = pick

        if best_pick_in_game:
            grouped.setdefault(date_key, []).append(best_pick_in_game)

    message = f"🛡️ **NBA V80.2 Quantum Sync**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    message += f"💰 目前本金：{TOTAL_BANKROLL} 元\n"

    if not grouped:
        message += "\n📭 今日無符合高偏差值場次。"
    else:
        for date, picks in grouped.items():
            message += f"\n📅 **{date} 強力精選**\n"
            # 只取當日勝算最高 (Edge 最大) 的前 4 場
            picks.sort(key=lambda x: x["edge"], reverse=True)
            top_picks = picks[:4]
            
            for p in top_picks:
                pt_str = f"{p['point']:+}" if p['point'] != 0 else ""
                message += f"**{p['match']}** {p['label']}\n"
                message += f"🎯 {TEAM_CN.get(p['team'],p['team'])} {pt_str} @ **{p['odds']}**\n"
                message += f"📊 預期勝率: **{p['prob']:.1%}** | EV: **+{p['ev']:.2f}**\n"
                message += f"💵 **建議下注：{p['bet']} 元**\n"
                message += "--------\n"
            
            # 當日串關建議 (鎖定同一天)
            if len(top_picks) >= 2:
                parlay = top_picks[:2]
                message += f"🔗 **今日精選 2x1 串關 (賠率 {(parlay[0]['odds']*parlay[1]['odds']):.2f})**\n"
                message += f"✅ {parlay[0]['match']} & {parlay[1]['match']}\n"
                message += f"💵 **建議金額：50 元**\n"
                message += "================\n"

    requests.post(WEBHOOK, json={"content": message})
    save_db({"history": history, "locks": locks})

if __name__=="__main__":
    run()
