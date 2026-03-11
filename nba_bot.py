import requests
import os
import math
from datetime import datetime, timedelta
from itertools import combinations

# ==========================================
# NBA V420 Sharp Syndicate - $1000 Bankroll
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# 策略與資金參數
MIN_EV_THRESHOLD = 0.15 
MIN_CLV = 0.0            
TOTAL_BANKROLL = 1000
KELLY_FRACTION = 0.25
MAX_SINGLE_BET = 150
PARLAY_BET_AMOUNT = 50

PACE = {"Boston Celtics":100,"Milwaukee Bucks":101,"Denver Nuggets":97,"Golden State Warriors":101,"Los Angeles Lakers":99,"Phoenix Suns":98,"Dallas Mavericks":102,"Los Angeles Clippers":101,"Miami Heat":96,"Philadelphia 76ers":100,"New York Knicks":99,"Toronto Raptors":98,"Chicago Bulls":97,"Atlanta Hawks":101,"Brooklyn Nets":100,"Cleveland Cavaliers":98,"Indiana Pacers":103,"Detroit Pistons":97,"Orlando Magic":96,"Charlotte Hornets":95,"Washington Wizards":99,"Houston Rockets":98,"San Antonio Spurs":96,"Memphis Grizzlies":97,"New Orleans Pelicans":95,"Minnesota Timberwolves":98,"Oklahoma City Thunder":102,"Utah Jazz":97,"Portland Trail Blazers":95,"Sacramento Kings":98}
TEAM_CN = {"Boston Celtics":"塞爾提克","Milwaukee Bucks":"公鹿","Denver Nuggets":"金塊","Golden State Warriors":"勇士","Los Angeles Lakers":"湖人","Phoenix Suns":"太陽","Dallas Mavericks":"獨行俠","Los Angeles Clippers":"快艇","Miami Heat":"熱火","Philadelphia 76ers":"七六人","New York Knicks":"尼克","Toronto Raptors":"暴龍","Chicago Bulls":"公牛","Atlanta Hawks":"老鷹","Brooklyn Nets":"籃網","Cleveland Cavaliers":"騎士","Indiana Pacers":"溜馬","Detroit Pistons":"活塞","Orlando Magic":"魔術","Charlotte Hornets":"黃蜂","Washington Wizards":"巫師","Houston Rockets":"火箭","San Antonio Spurs":"馬刺","Memphis Grizzlies":"灰熊","New Orleans Pelicans":"鵜鶘","Minnesota Timberwolves":"灰狼","Oklahoma City Thunder":"雷霆","Utah Jazz":"爵士","Portland Trail Blazers":"拓荒者","Sacramento Kings":"國王"}

def normal_cdf(x, mean, std):
    return 0.5 * (1 + math.erf((x - mean) / (std * math.sqrt(2))))

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    r = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions":"us","markets":"spreads","oddsFormat":"decimal"})
    if r.status_code != 200: return
    games = r.json()
    output_list = []

    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        if now_tw > (commence_tw + timedelta(minutes=150)): continue
        display_tw = commence_tw + timedelta(minutes=-10)
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")

        # 取得市場平均線 (Benchmark)
        all_lines = [o["point"] for b in g.get("bookmakers", []) for m in b["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home]
        if not all_lines: continue
        avg_market_line = sum(all_lines)/len(all_lines)

        for b in g.get("bookmakers", []):
            for m in b["markets"]:
                if m["key"] != "spreads": continue
                for o in m["outcomes"]:
                    # 計算 CLV 與 標竿
                    clv = (avg_market_line - o["point"]) if o["name"]==home else ((-avg_market_line) - o["point"])
                    benchmark = avg_market_line if o["name"]==home else -avg_market_line
                    
                    pace_val = PACE.get(o["name"], 100)
                    prob = 1 - normal_cdf(0, clv, 11.5 + pace_val*0.05)
                    ev = prob*(o["price"]-1)-(1-prob)

                    if ev >= MIN_EV_THRESHOLD and clv >= MIN_CLV:
                        b_val = o["price"] - 1
                        k_p = (b_val * prob - (1 - prob)) / b_val
                        bet_amt = round(max(0, min(TOTAL_BANKROLL * k_p * KELLY_FRACTION, MAX_SINGLE_BET)))

                        pick = {
                            "date": date_key, "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                            "team": TEAM_CN.get(o["name"], o["name"]), "label": "[受讓]" if o["point"]>0 else "[讓分]",
                            "point": f"{o['point']:+}", "benchmark": f"{benchmark:+.1f}",
                            "odds": o["price"], "ev": ev, "clv": clv, "bet": bet_amt,
                            "prob": prob, "time": display_tw.strftime("%H:%M")
                        }
                        output_list.append(pick)

    if not output_list:
        print("目前沒有符合條件的場次。")
        return

    # 組裝訊息
    msg = f"🛡️ **NBA V420 Sharp Syndicate Engine**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n🚫 過濾: EV < {MIN_EV_THRESHOLD} | CLV < {MIN_CLV}\n"

    current_date = ""
    for p in sorted(output_list, key=lambda x: (x['date'], -x['ev'])):
        if p['date'] != current_date:
            msg += f"\n📅 **{p['date']}**\n"
            current_date = p['date']

        msg += f"**{p['match']}** {p['label']}\n"
        msg += f"✨ ⏰ {p['time']} | 🎯 {p['team']} {p['point']} (標竿: {p['benchmark']}) @ **{p['odds']}**\n"
        msg += f"💰 EV: **+{p['ev']:.2f}** | 🎯 CLV: **{p['clv']:+.2f}**\n"
        msg += f"💵 **建議下注：{p['bet']} 元**\n"
        msg += f"📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **50.0%**\n"
        msg += "--------\n"

    # AI 串關推薦邏輯
    if len(output_list) >= 2:
        top_combos = []
        for n in [2, 3]:
            if len(output_list) < n: continue
            for combo in combinations(output_list, n):
                c_odds = 1
                c_ev = 0
                c_prob = 1
                for t in combo:
                    c_odds *= t["odds"]
                    c_ev += t["ev"]
                    c_prob *= t["prob"]
                # 綜合評分：EV 權重 60%，勝率權重 40%
                score = (c_ev / n) * 0.6 + c_prob * 0.4
                top_combos.append((score, combo, c_odds))
        
        if top_combos:
            best_combo = max(top_combos, key=lambda x: x[0])
            msg += f"\n💎 **AI 推薦串關 (賠率 {best_combo[2]:.2f})**\n"
            for t in best_combo[1]:
                msg += f"✅ {t['match']} {t['label']} ({t['team']} {t['point']})\n"
            msg += f"💵 **建議下注：{PARLAY_BET_AMOUNT} 元**\n"
            msg += "================\n"

    requests.post(WEBHOOK, json={"content": msg})

if __name__=="__main__":
    run()
