import requests
import os
import json
import random
from datetime import datetime, timedelta

# ==========================================
# NBA V91 Smart Spread Engine (EV>0.3 Only)
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
DB_FILE = "nba_v91_db.json"

TIME_OFFSET_MINUTES = -10
SIMS = 20000
EDGE_THRESHOLD = 0.03
PARLAY_EV_THRESHOLD = 0.05
HOME_ADV = 2.4
SPREAD_STD = 13.5
EV_MIN_DISPLAY = 0.3  # 只推 EV > 0.3

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
    "Boston Celtics":{"off":121,"def":110}, "Denver Nuggets":{"off":118,"def":112},
    "Oklahoma City Thunder":{"off":120,"def":111}, "Milwaukee Bucks":{"off":119,"def":113},
    "Minnesota Timberwolves":{"off":115,"def":108}, "Los Angeles Clippers":{"off":117,"def":112},
    "Dallas Mavericks":{"off":119,"def":114}, "Phoenix Suns":{"off":117,"def":113},
    "Golden State Warriors":{"off":118,"def":114}, "Los Angeles Lakers":{"off":116,"def":114},
    "New York Knicks":{"off":116,"def":111}, "Cleveland Cavaliers":{"off":115,"def":110},
    "Philadelphia 76ers":{"off":118,"def":113}, "Sacramento Kings":{"off":119,"def":115},
    "Miami Heat":{"off":112,"def":111}, "Indiana Pacers":{"off":122,"def":118},
    "Houston Rockets":{"off":114,"def":111}, "New Orleans Pelicans":{"off":116,"def":111},
    "Atlanta Hawks":{"off":118,"def":118}, "Chicago Bulls":{"off":111,"def":113},
    "Toronto Raptors":{"off":110,"def":115}, "Brooklyn Nets":{"off":111,"def":114},
    "Charlotte Hornets":{"off":108,"def":118}, "Detroit Pistons":{"off":109,"def":119},
    "Utah Jazz":{"off":112,"def":118}, "Portland Trail Blazers":{"off":108,"def":118},
    "San Antonio Spurs":{"off":112,"def":119}, "Washington Wizards":{"off":109,"def":120},
    "Memphis Grizzlies":{"off":113,"def":113}, "Orlando Magic":{"off":113,"def":110}
}

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE,"r") as f: return json.load(f)
    return {"history":{}, "locks": {}}

def save_db(db):
    with open(DB_FILE,"w") as f: json.dump(db,f,indent=2)

def predict_spread(home,away):
    h = TEAM_STATS.get(home, {"off":115,"def":115})
    a = TEAM_STATS.get(away, {"off":115,"def":115})
    return ((h["off"]-h["def"])-(a["off"]-a["def"]))/2 + HOME_ADV

def monte_spread(model,line):
    win = sum(1 for _ in range(SIMS) if random.gauss(model,SPREAD_STD)-line>0)
    return win/SIMS

def run():
    db = load_db()
    history, locks = db.get("history",{}), db.get("locks",{})
    now_tw = datetime.utcnow() + timedelta(hours=8)

    r = requests.get(f"{BASE_URL}/odds/",params={
        "apiKey":API_KEY,"regions":"us","markets":"spreads","oddsFormat":"decimal"
    },timeout=10)
    if r.status_code!=200: return
    games = r.json()

    grouped, parlay_candidates = {}, []

    for g in games:
        gid, home, away = g["id"], g["home_team"], g["away_team"]
        raw_time = datetime.strptime(g["commence_time"],"%Y-%m-%dT%H:%M:%SZ")+timedelta(hours=8)
        display_time = raw_time+timedelta(minutes=TIME_OFFSET_MINUTES)
        if now_tw > (display_time+timedelta(minutes=150)): continue
        date_key = display_time.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")
        is_live = now_tw >= (display_time - timedelta(minutes=5))

        best_pick = None
        for book in g.get("bookmakers",[]):
            for market in book.get("markets",[]):
                if market["key"]!="spreads": continue
                for o in market["outcomes"]:
                    model_val = predict_spread(home,away)
                    line = o["point"]
                    prob = monte_spread(model_val,line)
                    ev = (prob*(o["price"]-1))-(1-prob)
                    edge = prob - (1/o["price"])
                    if ev<EV_MIN_DISPLAY: continue
                    pick = {
                        "team":o["name"], "point":line, "odds":o["price"],
                        "prob":prob, "implied":1/o["price"], "ev":ev, "edge":edge,
                        "match":f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                        "time":display_time.strftime("%H:%M"),
                        "is_live":is_live
                    }
                    if not best_pick or ev>best_pick["ev"]: best_pick=pick

        if best_pick:
            grouped.setdefault(date_key,[]).append(best_pick)
            if best_pick["ev"]>PARLAY_EV_THRESHOLD and not best_pick["is_live"]: parlay_candidates.append(best_pick)

    # --- 構建 Discord 訊息 ---
    message=f"🛡️ **NBA V91 Smart Spread Engine**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    for date,picks in grouped.items():
        message+=f"\n📅 **{date}**\n"
        picks.sort(key=lambda x:x["ev"],reverse=True)
        for p in picks:
            live_tag="🔴 [場中] " if p["is_live"] else ""
            parlay_tag="🔗 " if p["ev"]>PARLAY_EV_THRESHOLD else ""
            label="[受讓]" if p["point"]>0 else "[讓分]"
            message+=f"{live_tag}{parlay_tag}**{p['match']}** {label}\n"
            message+=f"✨ ⏰ {p['time']} | 🎯 {TEAM_CN.get(p['team'],p['team'])} {p['point']:+} @ **{p['odds']}**\n"
            message+=f"💰 EV: **+{p['ev']:.2f}**\n"
            message+=f"📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **{p['implied']:.1%}**\n"
            message+=f"📈 領先 (Edge): **{p['edge']:+.2%}**\n--------\n"

    # --- 同日串關 ---
    if len(parlay_candidates)>=2:
        parlay_candidates.sort(key=lambda x:x["prob"],reverse=True)
        top=parlay_candidates[:2]
        message+=f"\n💎 **AI 今日同日組合 (讓分/受讓)**\n"
        for t in top:
            label="[受讓]" if t["point"]>0 else "[讓分]"
            message+=f"✅ {t['match']} ({label})\n"
        message+=f"📊 組合賠率: **{top[0]['odds']*top[1]['odds']:.2f}**\n"

    requests.post(WEBHOOK,json={"content":message})
    save_db({"history":{}, "locks":{}})

if __name__=="__main__":
    run()