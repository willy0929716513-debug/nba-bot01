import requests
import os
import math
from datetime import datetime, timedelta

# ==========================================
# NBA V170 Sharp Syndicate - CLV 0.25 Final
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# 策略與資金參數
MIN_EV_THRESHOLD = 0.30
MAX_PROB_CAP = 0.82
HOME_ADV = 2.4
DISPLAY_TIME_OFFSET = -10
SPREAD_STD_BASE = 12.5
MIN_CLV = 0.25  # 寬鬆版門檻：領先市場 0.25 分即標註
TOTAL_BANKROLL = 1000 # 初始本金 1000 元
KELLY_FRACTION = 0.25 # 穩健型凱利係數

PACE = {"Indiana Pacers":103,"Atlanta Hawks":101,"Golden State Warriors":101,"Oklahoma City Thunder":102,"Boston Celtics":100,"Denver Nuggets":97,"Miami Heat":96}
TEAM_CN = {"Boston Celtics":"塞爾提克","Milwaukee Bucks":"公鹿","Denver Nuggets":"金塊","Golden State Warriors":"勇士","Los Angeles Lakers":"湖人","Phoenix Suns":"太陽","Dallas Mavericks":"獨行俠","Los Angeles Clippers":"快艇","Miami Heat":"熱火","Philadelphia 76ers":"七六人","New York Knicks":"尼克","Toronto Raptors":"暴龍","Chicago Bulls":"公牛","Atlanta Hawks":"老鷹","Brooklyn Nets":"籃網","Cleveland Cavaliers":"騎士","Indiana Pacers":"溜馬","Detroit Pistons":"活塞","Orlando Magic":"魔術","Charlotte Hornets":"黃蜂","Washington Wizards":"巫師","Houston Rockets":"火箭","San Antonio Spurs":"馬刺","Memphis Grizzlies":"灰熊","New Orleans Pelicans":"鵜鶘","Minnesota Timberwolves":"灰狼","Oklahoma City Thunder":"雷霆","Utah Jazz":"爵士","Portland Trail Blazers":"拓荒者","Sacramento Kings":"國王"}
TEAM_STATS = {"Boston Celtics":{"off":121,"def":110},"Denver Nuggets":{"off":118,"def":112},"Oklahoma City Thunder":{"off":120,"def":111},"Milwaukee Bucks":{"off":119,"def":113},"Minnesota Timberwolves":{"off":115,"def":108},"Los Angeles Clippers":{"off":117,"def":112},"Dallas Mavericks":{"off":119,"def":114},"Phoenix Suns":{"off":117,"def":113},"Golden State Warriors":{"off":118,"def":114},"Los Angeles Lakers":{"off":116,"def":114},"New York Knicks":{"off":116,"def":111},"Cleveland Cavaliers":{"off":115,"def":110},"Philadelphia 76ers":{"off":118,"def":113},"Sacramento Kings":{"off":119,"def":115},"Miami Heat":{"off":112,"def":111},"Indiana Pacers":{"off":122,"def":118},"Houston Rockets":{"off":114,"def":111},"New Orleans Pelicans":{"off":116,"def":111},"Atlanta Hawks":{"off":118,"def":118},"Chicago Bulls":{"off":111,"def":113},"Toronto Raptors":{"off":110,"def":115},"Brooklyn Nets":{"off":111,"def":114},"Charlotte Hornets":{"off":108,"def":118},"Detroit Pistons":{"off":109,"def":119},"Utah Jazz":{"off":112,"def":118},"Portland Trail Blazers":{"off":108,"def":118},"San Antonio Spurs":{"off":112,"def":119},"Washington Wizards":{"off":109,"def":120},"Memphis Grizzlies":{"off":113,"def":113},"Orlando Magic":{"off":113,"def":110}}

def normal_cdf(x, m, s): return 0.5 * (1 + math.erf((x - m) / (s * math.sqrt(2))))
def predict_spread(h_t, a_t):
    h, a = TEAM_STATS.get(h_t, {"off":115,"def":115}), TEAM_STATS.get(a_t, {"off":115,"def":115})
    return ((h["off"]-h["def"]) - (a["off"]-a["def"])) / 2 + HOME_ADV

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    r = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions":"us","markets":"spreads","oddsFormat":"decimal"})
    if r.status_code != 200: return
    games, live_picks, pregame_grouped = r.json(), [], {}

    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")+timedelta(hours=8)
        if now_tw > (commence_tw+timedelta(minutes=150)): continue
        is_live, display_tw = now_tw > commence_tw, commence_tw + timedelta(minutes=DISPLAY_TIME_OFFSET)
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")

        model_spread = predict_spread(home, away)
        pace = abs(PACE.get(home, 99) - PACE.get(away, 99)) * 0.1
        market_lines = [o["point"] for book in g.get("bookmakers", []) for m in book["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home]
        if not market_lines: continue
        avg_market_line = sum(market_lines)/len(market_lines)

        if abs(model_spread - avg_market_line) < 1.5 or abs(avg_market_line) > 14: continue

        best_pick = None
        for book in g.get("bookmakers", []):
            for m in book["markets"]:
                if m.get("key") != "spreads": continue
                for o in m["outcomes"]:
                    target = model_spread if o["name"] == home else -model_spread
                    diff = target - o["point"]
                    std = SPREAD_STD_BASE + pace + abs(target)*0.4
                    prob = max(1-MAX_PROB_CAP, min(1-normal_cdf(0, diff, std), MAX_PROB_CAP))
                    if abs(o["point"]) > 10: prob *= 0.95
                    
                    ev = prob*(o["price"]-1)-(1-prob)
                    clv = (avg_market_line - o["point"]) if o["name"] == home else ((-avg_market_line) - o["point"])

                    if ev >= MIN_EV_THRESHOLD and clv >= MIN_CLV:
                        b = o["price"] - 1
                        k_p = (b * prob - (1 - prob)) / b
                        # 凱利注碼並限制單場最高 150 元
                        final_bet = round(max(0, min(TOTAL_BANKROLL * k_p * KELLY_FRACTION, 150)))
                        pick = {"match":f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}","team":TEAM_CN.get(o["name"],o["name"]),"label":"[受讓]" if o["point"]>0 else "[讓分]","point":f"{o['point']:+}", "odds":o["price"],"ev":ev,"prob":prob,"clv":clv,"bet":final_bet,"implied":1/o["price"],"time":display_tw.strftime("%H:%M"),"is_live":is_live}
                        if not best_pick or ev > best_pick["ev"]: best_pick = pick
        
        if best_pick:
            if is_live: live_picks.append(best_pick)
            else: pregame_grouped.setdefault(date_key, []).append(best_pick)

    # 輸出格式優化
    msg = f"🛡️ **NBA V170 Sharp Syndicate Engine**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n🚫 過濾: EV < {MIN_EV_THRESHOLD} | 深盤 > 14 | CLV < {MIN_CLV}\n"
    
    if live_picks:
        msg += "\n🔥 **[場中比賽 LIVE]**\n"
        for p in live_picks:
            msg += f"**{p['match']}** {p['label']}\n✨ 🎯 {p['team']} {p['point']} @ **{p['odds']}**\n💰 EV: **+{p['ev']:.2f}** | 🎯 CLV: **{p['clv']:+.2f}**\n💵 **建議下注：{p['bet']} 元**\n📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **{p['implied']:.1%}**\n--------\n"

    for date, picks in pregame_grouped.items():
        msg += f"\n📅 **{date}**\n"
        picks.sort(key=lambda x: (x["clv"], x["ev"]), reverse=True)
        for p in picks:
            msg += f"**{p['match']}** {p['label']}\n✨ ⏰ {p['time']} | 🎯 {p['team']} {p['point']} @ **{p['odds']}**\n💰 EV: **+{p['ev']:.2f}** | 🎯 CLV: **{p['clv']:+.2f}**\n💵 **建議下注：{p['bet']} 元**\n📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **{p['implied']:.1%}**\n--------\n"

    requests.post(WEBHOOK, json={"content": msg})

if __name__=="__main__": run()
