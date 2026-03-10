import requests
import os
import random
from datetime import datetime, timedelta

# ==========================================
# NBA V91.2 Smart Spread Engine + EV>0.3 串關
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"

TIME_OFFSET_MINUTES = -10
SIMS = 20000
EV_MIN_DISPLAY = 0.3
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

def predict_spread(home, away):
    h = TEAM_STATS.get(home, {"off":115,"def":115})
    a = TEAM_STATS.get(away, {"off":115,"def":115})
    return ((h["off"]-h["def"]) - (a["off"]-a["def"])) / 2 + HOME_ADV

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    r = requests.get(f"{BASE_URL}/odds/", params={"apiKey":API_KEY, "regions":"us", "markets":"spreads", "oddsFormat":"decimal"})
    if r.status_code != 200: return
    games = r.json()

    grouped = {}
    parlay_candidates = []

    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        display_time = commence_time + timedelta(minutes=TIME_OFFSET_MINUTES)
        if now_tw > (display_time + timedelta(minutes=150)): continue
        date_key = display_time.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")
        is_live = now_tw >= (display_time - timedelta(minutes=5))

        model_val = predict_spread(home, away)
        best_pick = None

        for book in g.get("bookmakers", []):
            for market in book["markets"]:
                if market["key"] != "spreads": continue
                for o in market["outcomes"]:
                    target_val = model_val if o["name"]==home else -model_val
                    prob = sum(1 for _ in range(SIMS) if random.gauss(target_val, SPREAD_STD) - o["point"] > 0) / SIMS
                    ev = prob * (o["price"]-1) - (1-prob)
                    if ev < EV_MIN_DISPLAY: continue

                    # 判斷讓分/受讓
                    if o["point"] > 0:
                        label = "[受讓]"
                    else:
                        label = "[讓分]" if prob>0.5 else "[受讓]"

                    pick = {
                        "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                        "team": TEAM_CN.get(o["name"],o["name"]),
                        "point": o["point"],
                        "odds": o["price"],
                        "prob": prob,
                        "ev": ev,
                        "edge": prob-(1/o["price"]),
                        "time": display_time.strftime("%H:%M"),
                        "label": label,
                        "is_live": is_live,
                        "date_key": date_key
                    }

                    if best_pick is None or ev>best_pick["ev"]: best_pick=pick

        if best_pick:
            if date_key not in grouped: grouped[date_key]=[]
            grouped[date_key].append(best_pick)
            if best_pick["ev"] > EV_MIN_DISPLAY and not best_pick["is_live"]:
                parlay_candidates.append(best_pick)

    # 輸出訊息
    message = f"🛡️ **NBA V91.2 Smart Spread Engine**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    for date, picks in grouped.items():
        message += f"\n📅 **{date}**\n"
        picks.sort(key=lambda x:x["ev"], reverse=True)
        for p in picks:
            live_tag = "🔴 [場中] " if p["is_live"] else ""
            message += f"{live_tag}🔗 **{p['match']}** {p['label']}\n"
            message += f"✨ ⏰ {p['time']} | 🎯 {p['team']} {p['point']:+} @ **{p['odds']}**\n"
            message += f"💰 EV: **+{p['ev']:.2f}**\n"
            message += f"📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **{1/p['odds']:.1%}**\n"
            message += f"📈 領先 (Edge): **{p['edge']:+.2%}**\n--------\n"

    # 同日串關 (EV>0.3)
    parlay_by_date = {}
    for c in parlay_candidates:
        if c["date_key"] not in parlay_by_date: parlay_by_date[c["date_key"]]=[]
        parlay_by_date[c["date_key"]].append(c)

    for date, cands in parlay_by_date.items():
        if len(cands)>=2:
            cands.sort(key=lambda x:x["ev"], reverse=True)
            top = cands[:2]
            message += f"\n💎 **AI 今日同日組合 (讓分/受讓)**\n"
            message += f"✅ {top[0]['match']} ({top[0]['label']})\n"
            message += f"✅ {top[1]['match']} ({top[1]['label']})\n"
            message += f"📊 組合賠率: **{top[0]['odds']*top[1]['odds']:.2f}**\n"

    if WEBHOOK: requests.post(WEBHOOK, json={"content": message})

if __name__=="__main__":
    run()