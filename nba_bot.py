import requests
import os
import math
from datetime import datetime, timedelta
from itertools import combinations

# ==========================================
# NBA V430 Sharp Syndicate - All Picks Debug
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# 策略參數
MIN_EV_THRESHOLD = 0.15  # EV > 0.15
MIN_CLV = 0.0             # CLV >= 0
TOTAL_BANKROLL = 1000
KELLY_FRACTION = 0.25
MAX_BET = 150

# NBA 全隊中文名稱
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

# NBA PACE 全隊
PACE = {
    "Boston Celtics":100,"Milwaukee Bucks":101,"Denver Nuggets":97,"Golden State Warriors":101,
    "Los Angeles Lakers":98,"Phoenix Suns":96,"Dallas Mavericks":102,"Los Angeles Clippers":99,
    "Miami Heat":96,"Philadelphia 76ers":98,"New York Knicks":99,"Toronto Raptors":97,
    "Chicago Bulls":95,"Atlanta Hawks":101,"Brooklyn Nets":100,"Cleveland Cavaliers":98,
    "Indiana Pacers":103,"Detroit Pistons":94,"Orlando Magic":96,"Charlotte Hornets":95,
    "Washington Wizards":97,"Houston Rockets":95,"San Antonio Spurs":96,"Memphis Grizzlies":97,
    "New Orleans Pelicans":96,"Minnesota Timberwolves":95,"Oklahoma City Thunder":102,
    "Utah Jazz":96,"Portland Trail Blazers":95,"Sacramento Kings":97
}

# 計算正態分布累積
def normal_cdf(x, m, s):
    return 0.5 * (1 + math.erf((x - m) / (s * math.sqrt(2))))

# 主程式
def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    r = requests.get(BASE_URL, params={
        "apiKey": API_KEY, "regions":"us","markets":"spreads","oddsFormat":"decimal"
    })
    if r.status_code != 200:
        print("API 請求失敗或沒比賽")
        return

    games = r.json()
    if not games:
        print("今天沒有比賽資料")
        return

    output_list = []

    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")+timedelta(hours=8)
        display_tw = commence_tw + timedelta(minutes=-10)
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一")\
            .replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")
        
        # 計算市場平均線
        all_lines = [o["point"] for b in g.get("bookmakers", []) for m in b["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home]
        if not all_lines: continue
        avg_market_line = sum(all_lines)/len(all_lines)

        # Pace 差距
        pace = abs(PACE.get(home,99) - PACE.get(away,99)) * 0.1

        for b in g.get("bookmakers", []):
            for m in b["markets"]:
                if m["key"] != "spreads": continue
                for o in m["outcomes"]:
                    # CLV = 比市場線更有價值
                    clv = (avg_market_line - o["point"]) if o["name"]==home else (-avg_market_line - o["point"])
                    # 用 CLV + Pace 計算勝率
                    prob = 1 - normal_cdf(0, clv, 11.5+pace)
                    ev = prob*(o["price"]-1) - (1-prob)

                    if ev >= MIN_EV_THRESHOLD and clv >= MIN_CLV:
                        # 凱利注碼
                        b_val = o["price"]-1
                        k_p = (b_val*prob - (1-prob))/b_val
                        bet_amt = round(max(0,min(TOTAL_BANKROLL * k_p * KELLY_FRACTION, MAX_BET)))

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
                            "is_live": now_tw>commence_tw,
                            "time": display_tw.strftime("%H:%M")
                        }
                        output_list.append(pick)

    if not output_list:
        print("目前沒有符合 EV>0.15 或 CLV>=0 的場次")
        return

    # Debug: 先印出全部結果讓你挑
    print("=== 所有符合條件的比賽 ===")
    for p in output_list:
        print(f"{p['date']} | {p['match']} | {p['team']} {p['point']} @ {p['odds']} | EV: {p['ev']:.2f} | CLV: {p['clv']:.2f} | 建議下注: {p['bet']}")

    # 組裝 Discord 訊息
    msg = f"🛡️ **NBA V430 Sharp Syndicate Engine**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n🚫 過濾: EV < {MIN_EV_THRESHOLD} | CLV < {MIN_CLV}\n"

    current_date = ""
    for p in sorted(output_list, key=lambda x: x['date']):
        if p['date'] != current_date:
            msg += f"\n📅 **{p['date']}**\n"
            current_date = p['date']
        msg += f"**{p['match']}** {p['label']}\n"
        msg += f"✨ ⏰ {p['time']} | 🎯 {p['team']} {p['point']} @ **{p['odds']}**\n"
        msg += f"💰 EV: **+{p['ev']:.2f}** | 🎯 CLV: **{p['clv']:+.2f}**\n"
        msg += f"💵 **建議下注：{p['bet']} 元**\n"
        msg += f"📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **50.0%**\n"
        msg += "--------\n"

    # Discord Webhook 發送
    requests.post(WEBHOOK, json={"content": msg})

if __name__=="__main__":
    run()