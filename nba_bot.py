import requests
import os
import random
import json
from datetime import datetime, timedelta

# ==========================================
# NBA V91.1 Quantum Pro - Spread & H2H Focus
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"

SIMS = 20000
EDGE_THRESHOLD = 0.045
PARLAY_THRESHOLD = 0.065
TIME_OFFSET_MINS = -10  # 開賽時間修正
HOME_ADV = 2.4
MARKET_BIAS = -1.2

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

# (TEAM_STATS 使用你提供的最新數據)
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

def predict_spread(home, away):
    h, a = TEAM_STATS[home], TEAM_STATS[away]
    rating = (h["off"]-h["def"]) - (a["off"]-a["def"])
    return rating/2 + HOME_ADV + MARKET_BIAS

def monte_spread(model, line):
    std = 12 + abs(model)*0.15 # 動態標準差優化
    diff = model - line
    win = sum(1 for _ in range(SIMS) if random.gauss(diff, std) > 0)
    return win / SIMS

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    r = requests.get(f"{BASE_URL}/odds/", params={"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"}, timeout=10)
    games = r.json()

    grouped_data = {}

    for g in games:
        home, away = g["home_team"], g["away_team"]
        raw_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        # 修正開賽時間 (-10 min)
        display_time = raw_time + timedelta(minutes=TIME_OFFSET_MINS)
        date_key = display_time.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")

        if now_tw > (raw_time + timedelta(minutes=150)): continue

        model = predict_spread(home, away)
        best_pick = None

        for book in g.get("bookmakers", []):
            for market in book["markets"]:
                for o in market["outcomes"]:
                    if market["key"] == "spreads":
                        if abs(o["point"]) > 13: continue # 過濾極端大分差
                        target = model if o["name"] == home else -model
                        prob = monte_spread(target, o["point"])
                        label = "[受讓]" if o["point"] > 0 else "[讓分]"
                        pt_str = f"{o['point']:+}"
                    elif market["key"] == "h2h":
                        target = model if o["name"] == home else -model
                        prob = monte_spread(target, 0)
                        label = "[獨贏]"
                        pt_str = ""
                    else: continue

                    implied = 1 / o["price"]
                    edge = prob - implied
                    ev = (prob * o["price"]) - 1

                    if edge > EDGE_THRESHOLD:
                        pick = {
                            "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                            "team": TEAM_CN.get(o["name"], o["name"]),
                            "label": label, "point": pt_str, "odds": o["price"],
                            "prob": prob, "implied": implied, "edge": edge, "ev": ev,
                            "time": display_time.strftime("%H:%M")
                        }
                        if not best_pick or ev > best_pick["ev"]:
                            best_pick = pick

        if best_pick:
            grouped_data.setdefault(date_key, []).append(best_pick)

    # 訊息構建
    message = f"🛡️ **NBA V91.1 Quantum Pro**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    
    if not grouped_data:
        message += "\n📭 目前無明顯獲利偏差標的。"
    else:
        for date, picks in grouped_data.items():
            message += f"\n📅 **{date}**\n"
            picks.sort(key=lambda x: x["edge"], reverse=True)
            
            daily_parlay = []
            for p in picks:
                parlay_icon = "🔗 " if p["edge"] > PARLAY_THRESHOLD else ""
                if p["edge"] > PARLAY_THRESHOLD: daily_parlay.append(p)

                message += f"{parlay_icon}**{p['match']}** {p['label']}\n"
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
