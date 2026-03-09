import requests
import os
import json
import random
from datetime import datetime, timedelta

# ==========================================
# NBA V78 Quantum Final Pro (All-in-One)
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
DB_FILE = "nba_v78_db.json"

SIMS = 20000
EDGE_THRESHOLD = 0.03
PARLAY_EV_THRESHOLD = 0.05
HOME_ADV = 2.4
SPREAD_STD = 13.5
TOTAL_STD = 14.0

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

# 確保這裡有完整的 TEAM_STATS 字典 (略)

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE,"r") as f: return json.load(f)
    return {"history":{}, "locks": {}}

def save_db(db):
    with open(DB_FILE,"w") as f: json.dump(db, f, indent=2)

def predict_spread(home, away):
    h, a = TEAM_STATS.get(home, {"off":115,"def":115}), TEAM_STATS.get(away, {"off":115,"def":115})
    return ((h["off"]-h["def"]) - (a["off"]-a["def"])) / 2 + HOME_ADV

def run():
    db = load_db()
    history, locks = db.get("history", {}), db.get("locks", {})
    now_tw = datetime.utcnow() + timedelta(hours=8)
    
    r = requests.get(f"{BASE_URL}/odds/", params={"apiKey":API_KEY, "regions":"us", "markets":"spreads,totals", "oddsFormat":"decimal"})
    games = r.json()
    
    grouped = {}
    parlay_candidates = []

    for g in games:
        gid, home, away = g["id"], g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        
        # 過濾已結束比賽
        if now_tw > (commence_tw + timedelta(minutes=150)): continue
        
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")
        is_live = now_tw >= (commence_tw - timedelta(minutes=5))

        books = g.get("bookmakers", [])
        if not books: continue
        
        best_pick = None
        for m_obj in books[0]["markets"]:
            market_key = m_obj["key"]
            for o in m_obj["outcomes"]:
                # CLV 邏輯
                hist_id = f"{gid}_{market_key}_{o.get('name', 'total')}"
                if hist_id not in history: history[hist_id] = o["price"]
                clv_val = (o["price"] - history[hist_id]) / history[hist_id]
                
                # 模擬與 EV 計算
                model_val = predict_spread(home, away)
                prob = sum(1 for _ in range(SIMS) if random.gauss(model_val if o["name"]==home else -model_val, SPREAD_STD) + o["point"] > 0) / SIMS if market_key == "spreads" else 0.5
                
                ev = (prob * (o["price"] - 1)) - (1 - prob)
                edge = prob - (1/o["price"])
                
                # 玩法分類
                label = "[受讓]" if o["point"] > 0 and market_key == "spreads" else ("[讓分]" if market_key == "spreads" else "[總分]")
                
                # 鎖定與門檻
                is_locked = (gid in locks and locks[gid].get("name") == o.get("name"))
                if edge > (0.015 if is_locked else EDGE_THRESHOLD):
                    pick = {
                        "team": o.get("name", "Total"), "point": o["point"], "odds": o["price"],
                        "prob": prob, "ev": ev, "clv": clv_val, "label": label, "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                        "time": commence_tw.strftime("%H:%M"), "locked": is_locked, "is_live": is_live, "edge": edge
                    }
                    if best_pick is None or ev > best_pick["ev"]: best_pick = pick

        if best_pick:
            if date_key not in grouped: grouped[date_key] = []
            grouped[date_key].append(best_pick)
            locks[gid] = {"name": best_pick["team"], "point": best_pick["point"]}
            if best_pick["ev"] > PARLAY_EV_THRESHOLD and best_pick["clv"] >= 0 and not best_pick["is_live"]:
                parlay_candidates.append(best_pick)

    # Discord 構建
    message = f"🛡️ **NBA V78 Quantum Final Pro**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    for date, picks in grouped.items():
        message += f"\n📅 **{date}**\n"
        picks.sort(key=lambda x: x["ev"], reverse=True)
        for p in picks:
            clv_str = f"{p['clv']:+.2%}" if p['clv'] != 0 else "↔️ 0.00%"
            clv_icon = "📈" if p["clv"] > 0 else ("📉" if p["clv"] < 0 else "")
            live_tag = "🔴 [場中] " if p["is_live"] else ""
            parlay_tag = "🔗 " if (p["ev"] > PARLAY_EV_THRESHOLD and p["clv"] >= 0) else ""
            
            message += f"{live_tag}{parlay_tag}**{p['match']}** {p['label']}\n"
            message += f"{'🔒' if p['locked'] else '✨'} ⏰ {p['time']} | 🎯 {TEAM_CN.get(p['team'],p['team'])} {p['point']:+} @ **{p['odds']}**\n"
            message += f"💰 EV: **+{p['ev']:.2f}** | CLV: {clv_icon} **{clv_str}**\n"
            message += f"📊 勝率: **{p['prob']:.1%}** | Edge: **{p['edge']:.2%}**\n"
            message += "--------\n"

    # 串關組合建議
    if len(parlay_candidates) >= 2:
        parlay_candidates.sort(key=lambda x: x["prob"], reverse=True)
        top = parlay_candidates[:2]
        message += f"\n💎 **AI 推薦串關 (賠率 {(top[0]['odds']*top[1]['odds']):.2f})**\n"
        message += f"✅ {top[0]['match']} {top[0]['label']}\n✅ {top[1]['match']} {top[1]['label']}\n"

    requests.post(WEBHOOK, json={"content": message})
    save_db({"history": history, "locks": locks})

if __name__=="__main__": run()
