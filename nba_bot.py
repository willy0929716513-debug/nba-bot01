import requests
import os
import math
from datetime import datetime, timedelta

# ==========================================
# NBA V175 Sharp Syndicate Engine
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# 策略參數
MIN_EV_THRESHOLD = 0.30
MIN_CLV = 0.25

TOTAL_BANKROLL = 1000
KELLY_FRACTION = 0.25
MAX_BET = 150

NBA_STD = 12.5

TEAM_CN = {
"Boston Celtics":"塞爾提克","Milwaukee Bucks":"公鹿","Denver Nuggets":"金塊",
"Golden State Warriors":"勇士","Los Angeles Lakers":"湖人","Phoenix Suns":"太陽",
"Dallas Mavericks":"獨行俠","Los Angeles Clippers":"快艇","Miami Heat":"熱火",
"Philadelphia 76ers":"七六人","New York Knicks":"尼克","Toronto Raptors":"暴龍",
"Chicago Bulls":"公牛","Atlanta Hawks":"老鷹","Brooklyn Nets":"籃網",
"Cleveland Cavaliers":"騎士","Indiana Pacers":"溜馬","Detroit Pistons":"活塞",
"Orlando Magic":"魔術","Charlotte Hornets":"黃蜂","Washington Wizards":"巫師",
"Houston Rockets":"火箭","San Antonio Spurs":"馬刺","Memphis Grizzlies":"灰熊",
"New Orleans Pelicans":"鵜鶘","Minnesota Timberwolves":"灰狼",
"Oklahoma City Thunder":"雷霆","Utah Jazz":"爵士","Portland Trail Blazers":"拓荒者",
"Sacramento Kings":"國王"
}

def normal_cdf(x,m,s):
    return 0.5*(1+math.erf((x-m)/(s*math.sqrt(2))))

def run():

    now_tw=datetime.utcnow()+timedelta(hours=8)

    r=requests.get(
        BASE_URL,
        params={
            "apiKey":API_KEY,
            "regions":"us",
            "markets":"spreads",
            "oddsFormat":"decimal"
        }
    )

    if r.status_code!=200:
        return

    games=r.json()

    output_list=[]

    for g in games:

        home=g["home_team"]
        away=g["away_team"]

        commence_tw=datetime.strptime(
            g["commence_time"],
            "%Y-%m-%dT%H:%M:%SZ"
        )+timedelta(hours=8)

        if now_tw>(commence_tw+timedelta(minutes=150)):
            continue

        display_tw=commence_tw+timedelta(minutes=-10)

        date_key=commence_tw.strftime("%m/%d (週%w)")\
        .replace("週0","週日")\
        .replace("週1","週一")\
        .replace("週2","週二")\
        .replace("週3","週三")\
        .replace("週4","週四")\
        .replace("週5","週五")\
        .replace("週6","週六")

        # 市場平均盤
        all_lines=[
            o["point"]
            for b in g.get("bookmakers",[])
            for m in b["markets"]
            if m["key"]=="spreads"
            for o in m["outcomes"]
            if o["name"]==home
        ]

        if len(all_lines)<2:
            continue

        avg_market_line=sum(all_lines)/len(all_lines)

        for b in g.get("bookmakers",[]):

            for m in b["markets"]:

                if m["key"]!="spreads":
                    continue

                for o in m["outcomes"]:

                    # CLV計算
                    if o["name"]==home:
                        clv=avg_market_line-o["point"]
                    else:
                        clv=(-avg_market_line)-o["point"]

                    # 勝率模型
                    prob=normal_cdf(clv,0,NBA_STD)

                    # EV
                    ev=prob*(o["price"]-1)-(1-prob)

                    # 過濾條件
                    if ev<MIN_EV_THRESHOLD:
                        continue

                    if clv<MIN_CLV:
                        continue

                    # Kelly bet
                    b_val=o["price"]-1
                    k_p=(b_val*prob-(1-prob))/b_val

                    bet_amt=TOTAL_BANKROLL*k_p*KELLY_FRACTION
                    bet_amt=min(bet_amt,MAX_BET)
                    bet_amt=round(max(0,bet_amt))

                    if bet_amt<5:
                        bet_amt=0

                    pick={
                        "date":date_key,
                        "match":f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                        "team":TEAM_CN.get(o["name"],o["name"]),
                        "label":"[受讓]" if o["point"]>0 else "[讓分]",
                        "point":f"{o['point']:+}",
                        "odds":o["price"],
                        "ev":ev,
                        "clv":clv,
                        "bet":bet_amt,
                        "prob":prob,
                        "time":display_tw.strftime("%H:%M")
                    }

                    output_list.append(pick)

    if not output_list:
        print("目前沒有符合 EV>0.3 且 CLV>0 的場次。")
        return

    # Sharp排序
    output_list.sort(
        key=lambda x:(x["clv"],x["ev"],x["prob"]),
        reverse=True
    )

    msg=f"🛡️ **NBA V175 Sharp Syndicate Engine**\n"
    msg+=f"⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    msg+=f"🚫 過濾: EV < {MIN_EV_THRESHOLD} | CLV < {MIN_CLV}\n"

    current_date=""

    for p in output_list:

        if p["date"]!=current_date:
            msg+=f"\n📅 **{p['date']}**\n"
            current_date=p["date"]

        msg+=f"**{p['match']}** {p['label']}\n"
        msg+=f"✨ ⏰ {p['time']} | 🎯 {p['team']} {p['point']} @ **{p['odds']}**\n"
        msg+=f"💰 EV: **+{p['ev']:.2f}** | 🎯 CLV: **{p['clv']:+.2f}**\n"
        msg+=f"💵 **建議下注：{p['bet']} 元**\n"
        msg+=f"📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **50.0%**\n"
        msg+="--------\n"

    requests.post(WEBHOOK,json={"content":msg})


if __name__=="__main__":
    run()