import requests
import os
import json
import random
from datetime import datetime, timedelta

# ==========================================
# NBA V79.2 Quantum Shield - EV Filtered
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
DB_FILE = "nba_v79_db.json"

SIMS = 25000 # 增加模擬次數提高穩定度
MIN_EV_THRESHOLD = 0.3  # 你的新要求：EV 小於 0.3 直接過濾
EDGE_THRESHOLD = 0.05
HOME_ADV = 2.4
SPREAD_STD = 14.2 # 調高標準差以對抗近期爆冷
DISPLAY_TIME_OFFSET = -10 

# ... (TEAM_CN 字典保持不變)

# 推薦更新這些數據或引入動態 ELO
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

# ... (load_db, save_db, predict_spread 保持不變)

def run():
    db = load_db()
    history, locks = db.get("history", {}), db.get("locks", {})
    now_tw = datetime.utcnow() + timedelta(hours=8)
    
    r = requests.get(f"{BASE_URL}/odds/", params={"apiKey":API_KEY, "regions":"us", "markets":"spreads,h2h", "oddsFormat":"decimal"})
    games = r.json()
    
    grouped = {}

    for g in games:
        gid, home, away = g["id"], g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        if now_tw > (commence_tw + timedelta(minutes=150)): continue
        
        display_tw = commence_tw + timedelta(minutes=DISPLAY_TIME_OFFSET)
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")

        books = g.get("bookmakers", [])
        if not books: continue
        
        best_pick = None
        for m_obj in books[0]["markets"]:
            market_key = m_obj["key"]
            for o in m_obj["outcomes"]:
                model_val = predict_spread(home, away)
                # 讓分與不讓分共用模擬邏輯
                line = o.get("point", 0)
                prob = sum(1 for _ in range(SIMS) if random.gauss(model_val if o["name"]==home else -model_val, SPREAD_STD) + line > 0) / SIMS
                
                ev = (prob * (o["price"] - 1)) - (1 - prob)
                edge = prob - (1/o["price"])
                
                # --- 核心過濾邏輯：EV 必須 >= 0.3 ---
                if ev < MIN_EV_THRESHOLD: continue
                
                label = "[受讓]" if line > 0 else "[讓分]"
                if market_key == "h2h": label = "[獨贏]"

                pick = {
                    "team": o["name"], "point": line, "odds": o["price"],
                    "prob": prob, "implied": 1/o["price"], "ev": ev, 
                    "label": label, "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                    "display_time": display_tw.strftime("%H:%M"), "edge": edge
                }
                if best_pick is None or ev > best_pick["ev"]: best_pick = pick

        if best_pick:
            grouped.setdefault(date_key, []).append(best_pick)

    message = f"🛡️ **NBA V79.2 Quantum Shield**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    message += f"⚠️ 已過濾 EV < {MIN_EV_THRESHOLD} 的低價值標的\n"
    
    if not grouped:
        message += "\n📭 目前無符合高 EV 要求的場次。"
    else:
        for date, picks in grouped.items():
            message += f"\n📅 **{date}**\n"
            picks.sort(key=lambda x: x["ev"], reverse=True)
            day_parlay = []
            
            for p in picks:
                day_parlay.append(p)
                message += f"**{p['match']}** {p['label']}\n"
                message += f"✨ ⏰ {p['display_time']} | 🎯 {TEAM_CN.get(p['team'],p['team'])} {p['point'] if p['point'] != 0 else '':+} @ **{p['odds']}**\n"
                message += f"💰 EV: **+{p['ev']:.2f}** | 📈 Edge: **{p['edge']:+.2%}**\n"
                message += f"📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **{p['implied']:.1%}**\n"
                message += "--------\n"
            
            if len(day_parlay) >= 2:
                top = day_parlay[:2]
                message += f"💎 **{date} AI 精選串關 (賠率 {(top[0]['odds']*top[1]['odds']):.2f})**\n"
                message += f"✅ {top[0]['match']} {top[0]['label']}\n✅ {top[1]['match']} {top[1]['label']}\n"
                message += "================\n"

    requests.post(WEBHOOK, json={"content": message})
    save_db({"history": history, "locks": locks})

if __name__=="__main__":
    run()
