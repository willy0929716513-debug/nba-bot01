import requests
import os
import datetime

# ===== 環境變數 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# ===== 中文隊名 =====
TEAM_CN = {
    "Lakers": "湖人",
    "Warriors": "勇士",
    "Celtics": "塞爾提克",
    "Bucks": "公鹿",
    "Nuggets": "金塊",
    "Thunder": "雷霆",
    "Suns": "太陽",
    "Clippers": "快艇",
    "Heat": "熱火",
    "76ers": "七六人",
    "Kings": "國王",
    "Pelicans": "鵜鶘",
    "Timberwolves": "灰狼",
    "Mavericks": "獨行俠",
    "Knicks": "尼克",
    "Magic": "魔術",
    "Hornets": "黃蜂",
    "Pistons": "活塞",
    "Raptors": "暴龍",
    "Bulls": "公牛",
    "Spurs": "馬刺",
    "Jazz": "爵士",
    "Nets": "籃網",
    "Hawks": "老鷹",
    "Cavaliers": "騎士",
    "Pacers": "溜馬",
    "Grizzlies": "灰熊",
    "Trail Blazers": "拓荒者"
}

# ===== Discord 分段 =====
def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        part = text[i:i+MAX]
        requests.post(WEBHOOK_URL, json={"content": part})

# ===== Kelly公式 =====
def kelly(prob, odds=1.91):
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 3))

# ===== 主分析 =====
def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal"
    }

    res = requests.get(BASE_URL, params=params)
    games = res.json()

    recommend_text = "**🔥推薦下注（職業模型V4）**\n"
    all_text = "\n\n全部比賽\n"

    for g in games:
        home_en = g["home_team"]
        away_en = g["away_team"]

        home = TEAM_CN.get(home_en, home_en)
        away = TEAM_CN.get(away_en, away_en)

        try:
            markets = g["bookmakers"][0]["markets"]
        except:
            continue

        h2h = None
        spread = None
        total = None

        for m in markets:
            if m["key"] == "h2h":
                h2h = m["outcomes"]
            elif m["key"] == "spreads":
                spread = m["outcomes"]
            elif m["key"] == "totals":
                total = m["outcomes"]

        if not h2h:
            continue

        # ===== 勝負 =====
        try:
            home_ml = [o for o in h2h if o["name"] == home_en][0]["price"]
            away_ml = [o for o in h2h if o["name"] == away_en][0]["price"]
        except:
            continue

        home_power = 1 / home_ml
        away_power = 1 / away_ml
        prob_home = home_power / (home_power + away_power)

        # ===== 讓分 =====
        home_spread = None
        spread_text = ""
        if spread:
            try:
                home_spread = [o for o in spread if o["name"] == home_en][0]["point"]
                spread_text = f"{home} {home_spread:+}"
            except:
                pass

        # ===== 大小分 =====
        total_point = None
        total_text = ""
        if total:
            try:
                total_point = total[0]["point"]
                total_text = str(total_point)
            except:
                pass

        # ===== 比賽資訊 =====
        game_line = f"\n{away} vs {home}\n"
        game_line += f"主勝率：{prob_home:.2f}\n"
        game_line += f"讓分：{spread_text}\n"
        game_line += f"大小分：{total_text}\n"

        recs = []

        # ===== 勝負推薦 =====
        if prob_home > 0.58:
            k = kelly(prob_home)
            if k > 0.03:
                recs.append(f"🔴🔥 勝負：{home} (Kelly {k})")

        elif prob_home < 0.42:
            k = kelly(1 - prob_home)
            if k > 0.03:
                recs.append(f"🔴🔥 勝負：{away} (Kelly {k})")

        # ===== 讓分推薦（看讓幾分）=====
        if home_spread is not None:

            if prob_home > 0.60:
                if home_spread <= -6:
                    recs.append(f"🔴🔥 讓分：{home} {home_spread:+}")
                if home_spread <= -8 and prob_home > 0.65:
                    recs.append(f"🔴🔥 讓分：{home} {home_spread:+}")

            elif prob_home < 0.40:
                if home_spread >= 6:
                    recs.append(f"🔴🔥 讓分：{away} {-home_spread:+}")
                if home_spread >= 8 and prob_home < 0.35:
                    recs.append(f"🔴🔥 讓分：{away} {-home_spread:+}")

        # ===== 大小分推薦 =====
        if total_point is not None:
            diff = abs(prob_home - 0.5)

            if diff > 0.18:
                recs.append(f"🔴🔥 大小分：小於 {total_point}")
            elif diff < 0.06:
                recs.append(f"🔴🔥 大小分：大於 {total_point}")

        # ===== 推薦區 =====
        if recs:
            recommend_text += game_line
            for r in recs:
                recommend_text += r + "\n"

        # ===== 全部比賽 =====
        all_text += game_line
        for r in recs:
            all_text += r + "\n"

    send_discord(recommend_text)
    send_discord(all_text)


# ===== 執行 =====
if __name__ == "__main__":
    print("執行時間:", datetime.datetime.now())
    analyze()
