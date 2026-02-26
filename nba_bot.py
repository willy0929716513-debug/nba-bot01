import requests
import os
from datetime import datetime, timedelta

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

EDGE_THRESHOLD = 0.03
KELLY_CAP = 0.08
SPREAD_COEF = 0.15
MAX_SPREAD = 12

TEAM_CN = {
    "Los Angeles Lakers": "湖人","Golden State Warriors": "勇士","Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿","Denver Nuggets": "金塊","Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽","LA Clippers": "快艇","Miami Heat": "熱火",
    "Philadelphia 76ers": "七六人","Sacramento Kings": "國王","New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼","Dallas Mavericks": "獨行俠","New York Knicks": "尼克",
    "Orlando Magic": "魔術","Charlotte Hornets": "黃蜂","Detroit Pistons": "活塞",
    "Toronto Raptors": "暴龍","Chicago Bulls": "公牛","San Antonio Spurs": "馬刺",
    "Utah Jazz": "爵士","Brooklyn Nets": "籃網","Atlanta Hawks": "老鷹",
    "Cleveland Cavaliers": "騎士","Indiana Pacers": "溜馬","Memphis Grizzlies": "灰熊",
    "Portland Trail Blazers": "拓荒者","Washington Wizards": "巫師","Houston Rockets": "火箭"
}

def cn(t): return TEAM_CN.get(t, t)

def kelly(prob, odds):
    b = odds - 1
    if prob <= 1/odds:
        return 0
    k = (prob*b - (1-prob)) / b
    k = max(0, k)
    return min(round(k,4), KELLY_CAP)

def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        requests.post(WEBHOOK_URL, json={"content": text[i:i+MAX]})

def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    try:
        res = requests.get(BASE_URL, params=params)
        res.raise_for_status()
        games = res.json()
    except Exception as e:
        send_discord(f"API錯誤: {e}")
        return

    all_picks = []

    for g in games:

        # 台灣時間過濾（避開凌晨）
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        if tw_time.hour < 6:
            continue

        home_en = g["home_team"]
        away_en = g["away_team"]
        home = cn(home_en)
        away = cn(away_en)

        bookmakers = g.get("bookmakers",[])
        if not bookmakers:
            continue

        markets = bookmakers[0].get("markets",[])
        h2h = next((m["outcomes"] for m in markets if m["key"]=="h2h"),None)
        spreads = next((m["outcomes"] for m in markets if m["key"]=="spreads"),None)

        if not h2h:
            continue

        try:
            h_ml = next(o for o in h2h if o["name"]==home_en)["price"]
            a_ml = next(o for o in h2h if o["name"]==away_en)["price"]
        except:
            continue

        # 去水市場機率
        p_home_base = (1/h_ml) / ((1/h_ml)+(1/a_ml))
        p_home = min(p_home_base + 0.03, 0.96)
        p_away = 1 - p_home

        game_options = []

        # ===== 不讓分 =====
        for team,prob,odds in [
            (home,p_home,h_ml),
            (away,p_away,a_ml)
        ]:
            edge = prob - (1/odds)
            k = kelly(prob, odds)
            if edge >= EDGE_THRESHOLD and k > 0:
                game_options.append({
                    "game": f"{away} @ {home}",
                    "pick": f"獨贏：{team}",
                    "edge": edge,
                    "kelly": k
                })

        # ===== 讓分盤 =====
        if spreads:
            for o in spreads:
                team_en = o["name"]
                point = o["point"]
                odds = o["price"]

                if abs(point) > MAX_SPREAD:
                    continue

                if team_en == home_en:
                    base_prob = p_home
                else:
                    base_prob = p_away

                p_spread = 0.5 + (base_prob - 0.5) * SPREAD_COEF
                edge = p_spread - (1/odds)
                k = kelly(p_spread, odds)

                if edge >= EDGE_THRESHOLD and k > 0:
                    label = f"讓分：{cn(team_en)} ({point:+})"
                    game_options.append({
                        "game": f"{away} @ {home}",
                        "pick": label,
                        "edge": edge,
                        "kelly": k
                    })

        if game_options:
            game_options.sort(key=lambda x:x["edge"],reverse=True)
            all_picks.append(game_options[0])

    # ===== 取前兩強 =====
    all_picks.sort(key=lambda x:x["edge"],reverse=True)
    top2 = all_picks[:2]

    msg = f"🔥 NBA V12 風控穩定版 - {datetime.now().strftime('%m/%d %H:%M')}\n---"

    if not top2:
        msg += "\n今日無符合條件場次（嚴格風控）"
        send_discord(msg)
        return

    for r in top2:
        icon = "💎" if r["kelly"] >= 0.06 else "✅"
        msg += f"\n🏀 {r['game']}"
        msg += f"\n> {icon} {r['pick']}"
        msg += f"\n> Edge：{r['edge']:.2%}"
        msg += f"\n> 建議倉位：{r['kelly']:.2%}\n"

    send_discord(msg)

if __name__ == "__main__":
    analyze()