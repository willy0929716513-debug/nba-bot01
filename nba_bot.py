import requests
import os
import math
from datetime import datetime, timedelta
from itertools import combinations

# ==========================================
# NBA V170 Sharp Syndicate Engine - Final
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

MIN_EV_THRESHOLD = 0.30
MAX_PROB_CAP = 0.82
HOME_ADV = 2.4
DISPLAY_TIME_OFFSET = -10
SPREAD_STD_BASE = 12.5
MIN_CLV = 0.5  # 至少領先市場 0.5 分才投

PACE = {
    "Indiana Pacers":103, "Atlanta Hawks":101, "Golden State Warriors":101,
    "Oklahoma City Thunder":102, "Boston Celtics":100, "Denver Nuggets":97, "Miami Heat":96
}

TEAM_CN = {
    "Boston Celtics":"塞爾提克","Milwaukee Bucks":"公鹿","Denver Nuggets":"金塊",
    "Golden State Warriors":"勇士","Los Angeles Lakers":"湖人","Phoenix Suns":"太陽",
    "Dallas Mavericks":"獨行俠","Los Angeles Clippers":"快艇","Miami Heat":"熱火",
    "Philadelphia 76ers":"七六人","New York Knicks":"尼克","Toronto Raptors":"暴龍",
    "Chicago Bulls":"公牛","Atlanta Hawks":"老鷹","Brooklyn Nets":"籃網",
    "Cleveland Cavaliers":"騎士","Indiana Pacers":"溜馬","Detroit Pistons":"活塞",
    "Orlando Magic":"魔術","Charlotte Hornets":"黃蜂","Washington Wizards":"巫師",
    "Houston Rockets":"火箭","San Antonio Spurs":"馬刺","Memphis Grizzlies":"灰熊",
    "New Orleans Pelicans":"鵜鶘","Minnesota Timberwolves":"灰狼","Oklahoma City Thunder":"雷霆",
    "Utah Jazz":"爵士","Portland Trail Blazers":"拓荒者","Sacramento Kings":"國王"
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

def normal_cdf(x, mean, std):
    return 0.5 * (1 + math.erf((x - mean) / (std * math.sqrt(2))))

def predict_spread(home, away):
    h = TEAM_STATS.get(home, {"off":115, "def":115})
    a = TEAM_STATS.get(away, {"off":115, "def":115})
    return ((h["off"] - h["def"]) - (a["off"] - a["def"])) / 2 + HOME_ADV

def pace_factor(home, away):
    p1, p2 = PACE.get(home, 99), PACE.get(away, 99)
    return abs(p1 - p2) * 0.1

def calc_prob(model, line, pace):
    diff = model - line
    std = SPREAD_STD_BASE + pace + abs(model)*0.4
    prob = 1 - normal_cdf(0, diff, std)
    if abs(line) > 10: prob *= 0.95
    return max(1 - MAX_PROB_CAP, min(prob, MAX_PROB_CAP))

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    r = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions":"us","markets":"spreads","oddsFormat":"decimal"})
    if r.status_code != 200: return
    
    games = r.json()
    live_picks = []
    pregame_grouped = {}

    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")+timedelta(hours=8)
        if now_tw > (commence_tw+timedelta(minutes=150)): continue
        
        is_live = now_tw > commence_tw
        display_tw = commence_tw + timedelta(minutes=DISPLAY_TIME_OFFSET)
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")

        model_spread = predict_spread(home, away)
        pace = pace_factor(home, away)

        # 取得主隊市場平均線
        market_lines = [o["point"] for book in g.get("bookmakers", []) for m in book["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home]
        if not market_lines: continue
        avg_market_line = sum(market_lines)/len(market_lines)

        # 基礎過濾：線差 < 1.5 或 盤口 > 14 則跳過
        if abs(model_spread - avg_market_line) < 1.5 or abs(avg_market_line) > 14: continue

        best_pick = None
        for book in g.get("bookmakers", []):
            for m in book["markets"]:
                if m["key"]!="spreads": continue
                for o in m["outcomes"]:
                    target = model_spread if o["name"]==home else -model_spread
                    prob = calc_prob(target, o["point"], pace)
                    ev = prob*(o["price"]-1)-(1-prob)
                    
                    # 修正 CLV 方向邏輯
                    clv = (avg_market_line - o["point"]) if o["name"] == home else ((-avg_market_line) - o["point"])

                    if ev >= MIN_EV_THRESHOLD and clv >= MIN_CLV:
                        pick = {
                            "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                            "team": TEAM_CN.get(o["name"], o["name"]),
                            "label": "[受讓]" if o["point"]>0 else "[讓分]",
                            "point": f"{o['point']:+}", "odds": o["price"],
                            "ev": ev, "prob": prob, "implied":1/o["price"], "edge":prob-1/o["price"],
                            "clv": clv, "time": display_tw.strftime("%H:%M"), "is_live": is_live
                        }
                        if not best_pick or ev > best_pick["ev"]: best_pick = pick
        
        if best_pick:
            if is_live: live_picks.append(best_pick)
            else: pregame_grouped.setdefault(date_key, []).append(best_pick)

    # 輸出訊息
    message = f"🛡️ **NBA V170 Sharp Syndicate Engine**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    message += f"🚫 過濾: EV < {MIN_EV_THRESHOLD} | 深盤 > 14 | CLV < {MIN_CLV}\n"

    if live_picks:
        message += "\n🔥 **[場中比賽 LIVE]**\n"
        for p in live_picks:
            message += f"**{p['match']}** {p['label']}\n✨ 🎯 {p['team']} {p['point']} @ **{p['odds']}**\n💰 EV: **+{p['ev']:.2f}** | 📊 勝率: **{p['prob']:.1%}** | 🎯 CLV: **{p['clv']:+.2f}**\n--------\n"

    for date, picks in pregame_grouped.items():
        message += f"\n📅 **{date}**\n"
        # 排序：優先排 CLV 優勢大且 EV 高的
        picks.sort(key=lambda x: (x["clv"], x["ev"]), reverse=True)
        for p in picks:
            message += f"**{p['match']}** {p['label']}\n✨ ⏰ {p['time']} | 🎯 {p['team']} {p['point']} @ **{p['odds']}**\n💰 EV: **+{p['ev']:.2f}** | 🎯 CLV: **{p['clv']:+.2f}**\n📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **{p['implied']:.1%}**\n--------\n"

        if len(picks) >= 2:
            best_combo = None
            for n in [2, 3]:
                if len(picks) < n: continue
                for combo in combinations(picks, n):
                    c_odds, c_ev, c_prob, c_clv = 1, 0, 1, 0
                    for t in combo:
                        c_odds *= t["odds"]; c_ev += t["ev"]; c_prob *= t["prob"]; c_clv += t["clv"]
                    # Sharp 評分公式
                    score = c_ev*0.5 + c_prob*0.3 + c_clv*0.2
                    if not best_combo or score > best_combo[0]: best_combo = (score, combo, c_odds)
            
            if best_combo:
                message += f"💎 **{date} AI 推薦串關 (賠率 {best_combo[2]:.2f})**\n"
                for t in best_combo[1]: message += f"✅ {t['match']} {t['label']}\n"
                message += "================\n"

    requests.post(WEBHOOK, json={"content": message})

if __name__=="__main__": run()
