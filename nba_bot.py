import requests
import os
import math
from datetime import datetime, timedelta
from itertools import combinations

# ==========================================
# NBA V400 Sharp Syndicate - EV 0.15 Threshold
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
INJURY_URL = "https://api.sportsdata.io/v4/nba/scores/json/PlayerGameStatsByDate"

# 策略與資金參數
MIN_EV_THRESHOLD = 0.15  # 調整 EV 下限
MIN_CLV = 0.01           # CLV 必須大於 0
TOTAL_BANKROLL = 1000
KELLY_FRACTION = 0.25
MAX_BET = 150

# Pace: 補滿所有球隊
PACE = {
    "Boston Celtics":100,"Milwaukee Bucks":101,"Denver Nuggets":97,"Golden State Warriors":101,
    "Los Angeles Lakers":99,"Phoenix Suns":98,"Dallas Mavericks":102,"Los Angeles Clippers":101,
    "Miami Heat":96,"Philadelphia 76ers":100,"New York Knicks":99,"Toronto Raptors":98,
    "Chicago Bulls":97,"Atlanta Hawks":101,"Brooklyn Nets":100,"Cleveland Cavaliers":98,
    "Indiana Pacers":103,"Detroit Pistons":97,"Orlando Magic":96,"Charlotte Hornets":95,
    "Washington Wizards":99,"Houston Rockets":98,"San Antonio Spurs":96,"Memphis Grizzlies":97,
    "New Orleans Pelicans":95,"Minnesota Timberwolves":98,"Oklahoma City Thunder":102,
    "Utah Jazz":97,"Portland Trail Blazers":95,"Sacramento Kings":98
}

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

def normal_cdf(x, m, s): 
    return 0.5 * (1 + math.erf((x - m)/(s*math.sqrt(2))))

def predict_spread(home, away):
    h = TEAM_STATS.get(home, {"off":115,"def":115})
    a = TEAM_STATS.get(away, {"off":115,"def":115})
    return ((h["off"]-h["def"])-(a["off"]-a["def"]))/2 + 2.4

def pace_factor(home, away):
    return abs(PACE.get(home, 100)-PACE.get(away, 100))*0.1

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    r = requests.get(BASE_URL, params={"apiKey": API_KEY,"regions":"us","markets":"spreads","oddsFormat":"decimal"})
    if r.status_code != 200: return
    games = r.json()

    live_picks = []
    pregame_grouped = []

    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")+timedelta(hours=8)
        if now_tw > (commence_tw + timedelta(minutes=150)): continue

        is_live = now_tw > commence_tw
        display_tw = commence_tw + timedelta(minutes=-10)
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")

        model_spread = predict_spread(home, away)
        pace = pace_factor(home, away)

        market_lines = [o["point"] for b in g.get("bookmakers", []) for m in b["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home]
        if not market_lines: continue
        avg_market_line = sum(market_lines)/len(market_lines)
        if abs(model_spread-avg_market_line)<1.5 or abs(avg_market_line)>14: continue

        best_pick = None
        for b in g.get("bookmakers", []):
            for m in b["markets"]:
                if m["key"]!="spreads": continue
                for o in m["outcomes"]:
                    target = model_spread if o["name"]==home else -model_spread
                    clv = (avg_market_line - o["point"]) if o["name"]==home else ((-avg_market_line)-o["point"])
                    prob = 1 - normal_cdf(0, clv, 11.5)
                    ev = prob*(o["price"]-1)-(1-prob)

                    if ev>=MIN_EV_THRESHOLD and clv>MIN_CLV:
                        b_val = o["price"]-1
                        k_p = (b_val*prob-(1-prob))/b_val
                        bet_amt = round(max(0,min(TOTAL_BANKROLL*k_p*KELLY_FRACTION, MAX_BET)))
                        pick = {
                            "date": date_key,
                            "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                            "team": TEAM_CN.get(o["name"],o["name"]),
                            "label": "[受讓]" if o["point"]>0 else "[讓分]",
                            "point": f"{o['point']:+}",
                            "odds": o["price"],
                            "ev": ev,
                            "clv": clv,
                            "bet": bet_amt,
                            "prob": prob,
                            "time": display_tw.strftime("%H:%M"),
                            "is_live": is_live
                        }
                        if is_live: live_picks.append(pick)
                        else: pregame_grouped.append(pick)

    # 組裝訊息
    msg = f"🛡️ **NBA V400 Sharp Syndicate Engine**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n🚫 過濾: EV < {MIN_EV_THRESHOLD} | CLV < 0\n"

    # 場中
    if live_picks:
        msg += "\n🔥 **[場中比賽 LIVE]**\n"
        for p in live_picks:
            msg += f"**{p['match']}** {p['label']}\n✨ ⏰ {p['time']} | 🎯 {p['team']} {p['point']} @ **{p['odds']}**\n💰 EV: **+{p['ev']:.2f}** | 🎯 CLV: **{p['clv']:+.2f}**\n💵 建議下注：{p['bet']} 元\n📊 勝率: **{p['prob']:.1%}**\n--------\n"

    # 賽前串關及單場
    if pregame_grouped:
        pregame_grouped.sort(key=lambda x: x["ev"], reverse=True)
        current_date = ""
        for p in pregame_grouped:
            if p["date"] != current_date:
                msg += f"\n📅 **{p['date']}**\n"
                current_date = p["date"]
            msg += f"**{p['match']}** {p['label']}\n✨ ⏰ {p['time']} | 🎯 {p['team']} {p['point']} @ **{p['odds']}**\n💰 EV: **+{p['ev']:.2f}** | 🎯 CLV: **{p['clv']:+.2f}**\n💵 建議下注：{p['bet']} 元\n📊 勝率: **{p['prob']:.1%}**\n--------\n"

        # 串關 (賽前)
        if len(pregame_grouped)>=2:
            for date in set([p["date"] for p in pregame_grouped]):
                date_picks = [p for p in pregame_grouped if p["date"]==date]
                if len(date_picks)<2: continue
                best_combo = None
                for n in [2,3]:
                    if len(date_picks)<n: continue
                    for combo in combinations(date_picks,n):
                        c_odds, c_ev, c_prob = 1,0,1
                        for t in combo:
                            c_odds *= t["odds"]
                            c_ev += t["ev"]
                            c_prob *= t["prob"]
                        score = c_ev*0.6 + c_prob*0.4
                        if not best_combo or score>best_combo[0]:
                            best_combo = (score,combo,c_odds)
                if best_combo:
                    msg += f"💎 **{date} AI 推薦串關 (賠率 {best_combo[2]:.2f})**\n"
                    for t in best_combo[1]:
                        msg += f"✅ {t['match']} {t['label']}\n"
                    msg += "================\n"

    requests.post(WEBHOOK, json={"content": msg})

if __name__=="__main__":
    run()