import requests
from datetime import datetime

# ===== 你的設定 =====
API_KEY = "4c7bb99948506cb694deb4dcbf43de76"

WEBHOOK = "https://discordapp.com/api/webhooks/1470301767785775145/pGwf_zhEOYLwhDwBrW1BzsUDlfDjC0vtHFgknuTo24jdV10Fd2tPtsNvZBHCSgOyuGIg"

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# ===== 中文隊名對照 =====
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

# ===== Discord分段（避免2000字錯誤）=====
def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        part = text[i:i+MAX]
        requests.post(WEBHOOK, json={"content": part})

# ===== Kelly公式 =====
def kelly(prob, odds=1.91):
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 3))

# ===== EMA實力模型（簡化版）=====
def team_power(moneyline):
    prob = 1 / moneyline
    return prob

# ===== 主要分析 =====
def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal"
    }

    res = requests.get(BASE_URL, params=params)
    games = res.json()

    recommend_text = "**🔥推薦下注（職業模型）**\n"
    all_text = "\n\n全部比賽\n"

    for g in games:
        home = TEAM_CN.get(g["home_team"], g["home_team"])
        away = TEAM_CN.get(g["away_team"], g["away_team"])

        try:
            book = g["bookmakers"][0]["markets"]
        except:
            continue

        h2h = None
        spread = None
        total = None

        for m in book:
            if m["key"] == "h2h":
                h2h = m["outcomes"]
            elif m["key"] == "spreads":
                spread = m["outcomes"]
            elif m["key"] == "totals":
                total = m["outcomes"]

        if not h2h:
            continue

        # ===== 勝負 =====
        home_ml = [o for o in h2h if TEAM_CN.get(o["name"], o["name"]) == home][0]["price"]
        away_ml = [o for o in h2h if TEAM_CN.get(o["name"], o["name"]) == away][0]["price"]

        home_power_val = team_power(home_ml)
        away_power_val = team_power(away_ml)

        prob_home = home_power_val / (home_power_val + away_power_val)

        # ===== 讓分 =====
        spread_text = ""
        if spread:
            home_spread = [o for o in spread if TEAM_CN.get(o["name"], o["name"]) == home][0]["point"]
            spread_text = f"{home} {home_spread:+}"

        # ===== 大小分 =====
        total_text = ""
        if total:
            total_point = total[0]["point"]
            total_text = f"{total_point}"

        # ===== 判斷推薦 =====
        game_line = f"\n{away} vs {home}\n"
        game_line += f"主勝率：{prob_home:.2f}\n"
        game_line += f"讓分：{spread_text}\n"
        game_line += f"大小分：{total_text}\n"

        recs = []

        # 勝負推薦
        if prob_home > 0.58:
            k = kelly(prob_home)
            if k > 0.03:
                recs.append(f"🔴🔥 勝負：{home} (Kelly {k})")
        elif prob_home < 0.42:
            k = kelly(1 - prob_home)
            if k > 0.03:
                recs.append(f"🔴🔥 勝負：{away} (Kelly {k})")

        # 讓分推薦
        if spread and abs(prob_home - 0.5) > 0.12:
            if prob_home > 0.62:
                recs.append(f"🔴🔥 讓分：{home} {home_spread:+}")
            elif prob_home < 0.38:
                recs.append(f"🔴🔥 讓分：{away} {-home_spread:+}")

        # 大小分推薦
        if total:
            if prob_home > 0.65 or prob_home < 0.35:
                recs.append(f"🔴🔥 大小分：小於 {total_point}")

        # 加入推薦區
        if recs:
            recommend_text += game_line
            for r in recs:
                recommend_text += r + "\n"

        # 全部比賽區
        all_text += game_line
        for r in recs:
            all_text += r + "\n"

    send_discord(recommend_text)
    send_discord(all_text)


# ===== 執行 =====
analyze()
