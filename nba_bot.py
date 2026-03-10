import requests
import os
import random
import json
from datetime import datetime, timedelta

# ==========================================
# NBA V100.1 Quantum ELO - Pure Same Day
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

SIMS = 20000
EDGE_THRESHOLD = 0.04
PARLAY_THRESHOLD = 0.06
HOME_ADV = 2.3
STD = 11.8
TIME_OFFSET = -10  # 開賽時間顯示修正

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

TEAM_RATING = {
    "Boston Celtics":1790, "Denver Nuggets":1760, "Oklahoma City Thunder":1750,
    "Milwaukee Bucks":1740, "Minnesota Timberwolves":1730, "Los Angeles Clippers":1720,
    "Dallas Mavericks":1710, "Phoenix Suns":1700, "Golden State Warriors":1690,
    "Los Angeles Lakers":1680, "New York Knicks":1670, "Cleveland Cavaliers":1660,
    "Philadelphia 76ers":1660, "Sacramento Kings":1650, "Miami Heat":1640,
    "Indiana Pacers":1630, "Houston Rockets":1620, "New Orleans Pelicans":1620,
    "Atlanta Hawks":1600, "Chicago Bulls":1580, "Toronto Raptors":1560,
    "Brooklyn Nets":1550, "Charlotte Hornets":1520, "Detroit Pistons":1510,
    "Utah Jazz":1500, "Portland Trail Blazers":1490, "San Antonio Spurs":1480,
    "Washington Wizards":1470, "Memphis Grizzlies":1600, "Orlando Magic":1650
}

def predict_spread(home, away):
    rating_diff = TEAM_RATING[home] - TEAM_RATING[away]
    return (rating_diff / 25) + HOME_ADV

def monte_carlo(model_spread, line):
    win = sum(1 for _ in range(SIMS) if random.gauss(model_spread, STD) > line)
    return win / SIMS

def run():
    now_utc = datetime.utcnow()
    r = requests.get(BASE_URL, params={"apiKey":API_KEY, "regions":"us", "markets":"h2h,spreads", "oddsFormat":"decimal"})
    if r.status_code != 200: return
    games = r.json()

    grouped_data = {}

    for g in games:
        home, away = g["home_team"], g["away_team"]
        raw_start = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
        if raw_start < now_utc: continue

        # 時間修正與日期分組
        display_time = raw_start + timedelta(hours=8, minutes=TIME_OFFSET)
        date_key = display_time.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")

        model_spread = predict_spread(home, away)
        best_pick_for_game = None

        for book in g.get("bookmakers", []):
            for market in book["markets"]:
                for o in market["outcomes"]:
                    if market["key"] == "spreads":
                        target = model_spread if o["name"] == home else -model_spread
                        prob = monte_carlo(target, o["point"])
                        label = "[受讓]" if o["point"] > 0 else "[讓分]"
                        pt_str = f"{o['point']:+}"
                    elif market["key"] == "h2h":
                        target = model_spread if o["name"] == home else -model_spread
                        prob = monte_carlo(target, 0)
                        label = "[獨贏]"
                        pt_str = ""
                    else: continue

                    edge = prob - (1/o["price"])
                    ev = (prob * o["price"]) - 1

                    if edge > EDGE_THRESHOLD:
                        pick = {
                            "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                            "team": TEAM_CN.get(o["name"], o["name"]),
                            "label": label, "point": pt_str, "odds": o["price"],
                            "prob": prob, "implied": 1/o["price"], "edge": edge, "ev": ev,
                            "time": display_time.strftime("%H:%M")
                        }
                        if not best_pick_for_game or ev > best_pick_for_game["ev"]:
                            best_pick_for_game = pick

        if best_pick_for_game:
            grouped_data.setdefault(date_key, []).append(best_pick_for_game)

    # 訊息組裝
    message = "🛡️ **NBA V100.1 Quantum ELO**\n"
    message += f"⏱ {(now_utc + timedelta(hours=8)).strftime('%m/%d %H:%M')}\n"

    for date, picks in grouped_data.items():
        message += f"\n📅 **{date}**\n"
        picks.sort(key=lambda x: x["edge"], reverse=True)
        
        daily_parlay = []
        for p in picks:
            parlay_tag = "🔗 " if p["edge"] > PARLAY_THRESHOLD else ""
            if p["edge"] > PARLAY_THRESHOLD: daily_parlay.append(p)

            message += f"{parlay_tag}**{p['match']}** {p['label']}\n"
            message += f"✨ ⏰ {p['time']} | 🎯 {p['team']} {p['point']} @ **{p['odds']}**\n"
            message += f"💰 EV: **{p['ev']:+.2f}** | 📈 Edge: **{p['edge']:+.2%}**\n"
            message += f"📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **{p['implied']:.1%}**\n"
            message += "--------\n"

        if len(daily_parlay) >= 2:
            daily_parlay.sort(key=lambda x: x["edge"], reverse=True)
            c1, c2 = daily_parlay[0], daily_parlay[1]
            message += f"💎 **AI 推薦同日串關 (組合賠率 {c1['odds']*c2['odds']:.2f})**\n"
            message += f"✅ {c1['match']} {c1['label']}\n✅ {c2['match']} {c2['label']}\n"
            message += "================\n"

    requests.post(WEBHOOK, json={"content": message})

if __name__ == "__main__": run()
