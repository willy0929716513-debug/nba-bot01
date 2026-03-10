import requests
import os
import json
import random
import math
from datetime import datetime, timedelta

# ==========================================
# NBA V90.1 Quantum Sync - Pure Same Day
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"

# --- 核心量化參數 ---
SIMS = 20000
EDGE_THRESHOLD = 0.045
PARLAY_THRESHOLD = 0.065   # 同日串關門檻較高
HOME_ADV = 2.4
MARKET_BIAS = -1.2
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

# (TEAM_STATS 保持你提供的原始數值不變)
TEAM_STATS = {
    "Boston Celtics":{"off":121,"def":110,"pace":99}, "Denver Nuggets":{"off":118,"def":112,"pace":97},
    "Oklahoma City Thunder":{"off":120,"def":111,"pace":101}, "Milwaukee Bucks":{"off":119,"def":113,"pace":100},
    "Minnesota Timberwolves":{"off":115,"def":108,"pace":97}, "Los Angeles Clippers":{"off":117,"def":112,"pace":98},
    "Dallas Mavericks":{"off":119,"def":114,"pace":100}, "Phoenix Suns":{"off":117,"def":113,"pace":99},
    "Golden State Warriors":{"off":118,"def":114,"pace":101}, "Los Angeles Lakers":{"off":116,"def":114,"pace":101},
    "New York Knicks":{"off":116,"def":111,"pace":95}, "Cleveland Cavaliers":{"off":115,"def":110,"pace":96},
    "Philadelphia 76ers":{"off":118,"def":113,"pace":97}, "Sacramento Kings":{"off":119,"def":115,"pace":101},
    "Miami Heat":{"off":112,"def":111,"pace":95}, "Indiana Pacers":{"off":122,"def":118,"pace":103},
    "Houston Rockets":{"off":114,"def":111,"pace":98}, "New Orleans Pelicans":{"off":116,"def":111,"pace":98},
    "Atlanta Hawks":{"off":118,"def":118,"pace":102}, "Chicago Bulls":{"off":111,"def":113,"pace":97},
    "Toronto Raptors":{"off":110,"def":115,"pace":100}, "Brooklyn Nets":{"off":111,"def":114,"pace":98},
    "Charlotte Hornets":{"off":108,"def":118,"pace":101}, "Detroit Pistons":{"off":109,"def":119,"pace":100},
    "Utah Jazz":{"off":112,"def":118,"pace":101}, "Portland Trail Blazers":{"off":108,"def":118,"pace":99},
    "San Antonio Spurs":{"off":112,"def":119,"pace":101}, "Washington Wizards":{"off":109,"def":120,"pace":102},
    "Memphis Grizzlies":{"off":113,"def":113,"pace":100}, "Orlando Magic":{"off":113,"def":110,"pace":97}
}

def predict_spread(home, away):
    h, a = TEAM_STATS[home], TEAM_STATS[away]
    rating = (h["off"] - h["def"]) - (a["off"] - a["def"])
    return rating/2 + HOME_ADV + MARKET_BIAS

def predict_total(home, away):
    h, a = TEAM_STATS[home], TEAM_STATS[away]
    pace = (h["pace"] + a["pace"]) / 2
    off = (h["off"] + a["off"]) / 2
    return pace/100 * (off * 2)

def monte_carlo(model_val, line, std):
    diff = model_val - line
    win = sum(1 for _ in range(SIMS) if random.gauss(diff, std) > 0)
    return win / SIMS

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    r = requests.get(f"{BASE_URL}/odds/", params={"apiKey": API_KEY, "regions": "us", "markets": "spreads,totals", "oddsFormat": "decimal"})
    if r.status_code != 200: return
    games = r.json()

    grouped = {} # 按日期存放標的

    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        date_key = commence.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")
        
        # 排除已結束場次
        if now_tw > (commence + timedelta(minutes=150)): continue

        spread_model = predict_spread(home, away)
        total_model = predict_total(home, away)

        best_pick_for_game = None

        for book in g.get("bookmakers", []):
            for market in book["markets"]:
                for o in market["outcomes"]:
                    if market["key"] == "spreads":
                        # 計算預測隊伍勝率
                        target = spread_model if o["name"] == home else -spread_model
                        prob = monte_carlo(target, o["point"], SPREAD_STD)
                        label = "[讓分]" if o["point"] < 0 else "[受讓]"
                    elif market["key"] == "totals":
                        # 大小分勝率 (Over 使用 model - line, Under 使用 line - model)
                        prob = monte_carlo(total_model, o["point"], TOTAL_STD) if o["name"] == "Over" else monte_carlo(o["point"], total_model, TOTAL_STD)
                        label = "[總分]"
                    else: continue

                    implied = 1 / o["price"]
                    edge = prob - implied
                    ev = (prob * o["price"]) - 1

                    if edge > EDGE_THRESHOLD:
                        pick = {
                            "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                            "pick_name": f"{TEAM_CN.get(o['name'], o['name'])} {o['point']:+}",
                            "odds": o["price"], "prob": prob, "implied": implied, "edge": edge, "ev": ev,
                            "label": label, "time": commence.strftime("%H:%M"), "is_live": now_tw >= (commence - timedelta(minutes=5))
                        }
                        if not best_pick_for_game or ev > best_pick_for_game["ev"]:
                            best_pick_for_game = pick

        if best_pick_for_game:
            grouped.setdefault(date_key, []).append(best_pick_for_game)

    # 訊息組裝
    message = f"🛡️ **NBA V90.1 Quantum Sync**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    
    if not grouped:
        message += "\n📭 目前無穩定偏差場次。"
    else:
        for date, picks in grouped.items():
            message += f"\n📅 **{date}**\n"
            picks.sort(key=lambda x: x["edge"], reverse=True)
            
            daily_candidates = [] # 用於該日串關
            for p in picks:
                live_tag = "🔴 [場中] " if p["is_live"] else ""
                parlay_tag = "🔗 " if p["edge"] > PARLAY_THRESHOLD and not p["is_live"] else ""
                if p["edge"] > PARLAY_THRESHOLD and not p["is_live"]: daily_candidates.append(p)

                message += f"{live_tag}{parlay_tag}**{p['match']}** {p['label']}\n"
                message += f"✨ ⏰ {p['time']} | 🎯 {p['pick_name']} @ **{p['odds']}**\n"
                message += f"💰 EV: **{p['ev']:+.2f}** | 📈 Edge: **{p['edge']:+.2%}**\n"
                message += f"📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **{p['implied']:.1%}**\n"
                message += "--------\n"

            # 顯示該日串關
            if len(daily_candidates) >= 2:
                daily_candidates.sort(key=lambda x: x["edge"], reverse=True)
                c1, c2 = daily_candidates[0], daily_candidates[1]
                message += f"💎 **AI 推薦同日串關 (組合賠率 {c1['odds']*c2['odds']:.2f})**\n"
                message += f"✅ {c1['match']} {c1['label']}\n✅ {c2['match']} {c2['label']}\n"
                message += "================\n"

    requests.post(WEBHOOK, json={"content": message})

if __name__ == "__main__": run()
