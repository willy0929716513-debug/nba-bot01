import requests
import os
from datetime import datetime, timedelta

# ===== 環境變數 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# ===== 風控參數微調 =====
EDGE_THRESHOLD = 0.02   # 降低門檻至 2%，提高出手率
KELLY_CAP = 0.08        # 單場最高倉位 8%
SPREAD_COEF = 0.22      # 提升讓分盤敏感度
MAX_SPREAD = 12.5       # 過濾超過 12.5 的深盤

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

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
    if prob <= 1/odds: return 0
    k = (prob * b - (1 - prob)) / b
    return min(round(max(0, k), 4), KELLY_CAP)

def send_discord(text):
    MAX_LEN = 1900
    for i in range(0, len(text), MAX_LEN):
        requests.post(WEBHOOK_URL, json={"content": text[i:i+MAX_LEN]})

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
        send_discord(f"⚠️ API錯誤: {e}")
        return

    all_picks = []

    for g in games:
        # 1. 台灣時間過濾 (保留 06:00 之後的場次)
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        if tw_time.hour < 6: continue

        home_en, away_en = g["home_team"], g["away_team"]
        home, away = cn(home_en), cn(away_en)

        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue

        markets = bookmakers[0].get("markets", [])
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)

        if not h2h: continue

        try:
            h_ml = next(o for o in h2h if o["name"] == home_en)["price"]
            a_ml = next(o for o in h2h if o["name"] == away_en)["price"]
        except: continue

        # 2. 計算模型機率
        p_home_base = (1/h_ml) / ((1/h_ml) + (1/a_ml))
        p_home = min(p_home_base + 0.035, 0.96) # 稍微加強主場權重至 3.5%
        p_away = 1 - p_home

        game_options = []

        # (A) 不讓分 (ML)
        for team_en, prob, odds in [(home_en, p_home, h_ml), (away_en, p_away, a_ml)]:
            edge = prob - (1/odds)
            if edge > 0:
                game_options.append({
                    "game": f"{away} @ {home}",
                    "pick": f"獨贏：{cn(team_en)}",
                    "edge": edge,
                    "kelly": kelly(prob, odds)
                })

        # (B) 讓分盤 (Spread)
        if spreads:
            for o in spreads:
                point, odds = o["point"], o["price"]
                if abs(point) > MAX_SPREAD: continue

                base_prob = p_home if o["name"] == home_en else p_away
                p_spread = 0.5 + (base_prob - 0.5) * SPREAD_COEF
                edge = p_spread - (1/odds)

                prefix = "受讓" if point > 0 else "讓分"
                game_options.append({
                    "game": f"{away} @ {home}",
                    "pick": f"{prefix}：{cn(o['name'])} ({point:+})",
                    "edge": edge,
                    "kelly": kelly(p_spread, odds)
                })

        if game_options:
            game_options.sort(key=lambda x: x["edge"], reverse=True)
            all_picks.append(game_options[0])

    # 3. 輸出篩選
    qualified = [p for p in all_picks if p["edge"] >= EDGE_THRESHOLD]
    qualified.sort(key=lambda x: x["edge"], reverse=True)

    msg = f"🔥 NBA V12.1 精選推薦 - {datetime.now().strftime('%m/%d %H:%M')}\n---"

    if not qualified:
        all_picks.sort(key=lambda x: x["edge"], reverse=True)
        if all_picks:
            top = all_picks[0]
            msg += f"\n⚠️ 今日無達標場次，最優遺珠：\n🏀 {top['game']}\n> 💡 {top['pick']}\n> Edge：{top['edge']:.2%}\n> (未達 {EDGE_THRESHOLD:.0%} 門檻，建議觀望)"
        else:
            msg += "\n今日無符合條件之場次。"
    else:
        for r in qualified[:2]: # 取前兩強
            icon = "💎" if r["edge"] >= 0.04 else "✅"
            msg += f"\n🏀 {r['game']}\n> {icon} **{r['pick']}**\n> 預期優勢 (Edge)：{r['edge']:.2%}\n> 建議倉位：{r['kelly']:.2%}\n"

    send_discord(msg)

if __name__ == "__main__":
    analyze()
