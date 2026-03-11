import requests
import os
import math
from datetime import datetime, timedelta

# NBA V420 Sharp Syndicate - Final Version
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# 策略參數
MIN_EV_THRESHOLD = 0.05  # 調低門檻確保測試成功
TOTAL_BANKROLL = 1000
KELLY_FRACTION = 0.25

TEAM_CN = {"Boston Celtics":"塞爾提克","Milwaukee Bucks":"公鹿","Denver Nuggets":"金塊","Golden State Warriors":"勇士","Los Angeles Lakers":"湖人","Phoenix Suns":"太陽","Dallas Mavericks":"獨行俠","Los Angeles Clippers":"快艇","Miami Heat":"熱火","Philadelphia 76ers":"七六人","New York Knicks":"尼克","Toronto Raptors":"暴龍","Chicago Bulls":"公牛","Atlanta Hawks":"老鷹","Brooklyn Nets":"籃網","Cleveland Cavaliers":"騎士","Indiana Pacers":"溜馬","Detroit Pistons":"活塞","Orlando Magic":"魔術","Charlotte Hornets":"黃蜂","Washington Wizards":"巫師","Houston Rockets":"火箭","San Antonio Spurs":"馬刺","Memphis Grizzlies":"灰熊","New Orleans Pelicans":"鵜鶘","Minnesota Timberwolves":"灰狼","Oklahoma City Thunder":"雷霆","Utah Jazz":"爵士","Portland Trail Blazers":"拓荒者","Sacramento Kings":"國王"}

def normal_cdf(x, mean, std):
    return 0.5 * (1 + math.erf((x - mean) / (std * math.sqrt(2))))

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    
    if not API_KEY or not WEBHOOK:
        print("❌ 錯誤：環境變數 (Secrets) 沒設定好。")
        return

    params = {"apiKey": API_KEY, "regions": "us", "markets": "spreads", "oddsFormat": "decimal"}
    try:
        r = requests.get(BASE_URL, params=params)
        r.raise_for_status()
    except Exception as e:
        requests.post(WEBHOOK, json={"content": f"❌ API 請求失敗：{str(e)}"})
        return

    games = r.json()
    output_list = []

    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        
        # 只看未來 24 小時內的比賽
        if abs((commence_tw - now_tw).total_seconds()) > 86400: continue
        
        all_lines = [o["point"] for b in g.get("bookmakers", []) for m in b["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home]
        if not all_lines: continue
        avg_line = sum(all_lines) / len(all_lines)

        for b in g.get("bookmakers", []):
            for m in b["markets"]:
                if m["key"] != "spreads": continue
                for o in m["outcomes"]:
                    clv = (avg_line - o["point"]) if o["name"] == home else ((-avg_line) - o["point"])
                    prob = 1 - normal_cdf(0, clv, 12.0)
                    ev = prob * (o["price"] - 1) - (1 - prob)

                    if ev >= MIN_EV_THRESHOLD:
                        k_p = ((o["price"]-1)*prob - (1-prob)) / (o["price"]-1)
                        bet = round(max(0, min(TOTAL_BANKROLL * k_p * KELLY_FRACTION, 150)))
                        output_list.append({
                            "match": f"{TEAM_CN.get(away, away)} @ {TEAM_CN.get(home, home)}",
                            "team": TEAM_CN.get(o["name"], o["name"]),
                            "point": f"{o['point']:+}",
                            "avg": f"{avg_line if o['name']==home else -avg_line:+.1f}",
                            "odds": o["price"], "ev": ev, "bet": bet
                        })

    if not output_list:
        requests.post(WEBHOOK, json={"content": f"🔍 診斷回報：分析了 {len(games)} 場比賽，但目前沒有 EV > {MIN_EV_THRESHOLD} 的標的。"})
        return

    msg = f"🛡️ **NBA Sharp Syndicate**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    for p in output_list[:6]:
        msg += f"**{p['match']}**\n🎯 {p['team']} {p['point']} (標竿 {p['avg']}) @ **{p['odds']}**\n💰 EV: +{p['ev']:.2f} | 💵 建議: {p['bet']}元\n--------\n"
    
    requests.post(WEBHOOK, json={"content": msg})

if __name__ == "__main__":
    run()
