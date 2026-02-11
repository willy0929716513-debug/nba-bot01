import requests
import os
import datetime

# ===== 環境變數 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# ===== 環境檢查 =====
if not API_KEY:
    raise ValueError("ODDS_API_KEY 沒有設定")

if not WEBHOOK_URL:
    raise ValueError("DISCORD_WEBHOOK 沒有設定")

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# ===== 中文隊名對照（完整英文名 + fallback簡寫） =====
TEAM_CN = {
    "Los Angeles Lakers": "湖人",
    "Golden State Warriors": "勇士",
    "Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿",
    "Denver Nuggets": "金塊",
    "Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽",
    "Los Angeles Clippers": "快艇",
    "Miami Heat": "熱火",
    "Philadelphia 76ers": "七六人",
    "Sacramento Kings": "國王",
    "New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼",
    "Dallas Mavericks": "獨行俠",
    "New York Knicks": "尼克",
    "Orlando Magic": "魔術",
    "Charlotte Hornets": "黃蜂",
    "Detroit Pistons": "活塞",
    "Toronto Raptors": "暴龍",
    "Chicago Bulls": "公牛",
    "San Antonio Spurs": "馬刺",
    "Utah Jazz": "爵士",
    "Brooklyn Nets": "籃網",
    "Atlanta Hawks": "老鷹",
    "Cleveland Cavaliers": "騎士",
    "Indiana Pacers": "溜馬",
    "Memphis Grizzlies": "灰熊",
    "Portland Trail Blazers": "拓荒者"
}

def get_team_cn(name):
    # 先找完整名稱
    if name in TEAM_CN:
        return TEAM_CN[name]
    # 再找簡寫（最後一個單字）
    short = name.split()[-1]
    return TEAM_CN.get(short, name)

# ===== Discord 分段發送 =====
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

    try:
        res = requests.get(BASE_URL, params=params, timeout=10)
    except requests.exceptions.RequestException as e:
        send_discord(f"❌ 抓取 Odds API 失敗: {e}")
        return

    if res.status_code != 200:
        send_discord(f"❌ API回傳錯誤 {res.status_code}:\n{res.text}")
        return

    try:
        games = res.json()
    except:
        send_discord(f"❌ Odds API 回傳非JSON:\n{res.text}")
        return

    if not games:
        send_discord("❌ 今日沒有比賽資料")
        return

    recommend_text = "**🔥推薦下注（職業模型V4）**\n"
    all_text = "\n\n全部比賽\n"

    for g in games:
        home_en = g["home_team"]
        away_en = g["away_team"]

        home = get_team_cn(home_en)
        away = get_team_cn(away_en)

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

        # ===== 讓分推薦（正負都顯示） =====
        if home_spread is not None:
            # 主勝率高，推薦主隊讓分
            if prob_home > 0.60:
                if home_spread < 0:
                    recs.append(f"🔴🔥 讓分：{home} {home_spread:+}")
                else:
                    recs.append(f"🔴🔥 讓分：{away} {abs(home_spread):+}")
            # 主勝率低，推薦客隊讓分
            elif prob_home < 0.40:
                if home_spread < 0:
                    recs.append(f"🔴🔥 讓分：{away} {abs(home_spread):+}")
                else:
                    recs.append(f"🔴🔥 讓分：{home} {home_spread:+}")

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

        # ===== 全部比賽區 =====
        all_text += game_line
        for r in recs:
            all_text += r + "\n"

    # ===== 發送 Discord =====
    send_discord(recommend_text)
    send_discord(all_text)


# ===== 主程式執行 =====
if __name__ == "__main__":
    print("執行時間:", datetime.datetime.now())
    analyze()
