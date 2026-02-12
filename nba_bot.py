import os
import requests

# 環境變數
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not ODDS_API_KEY:
    raise ValueError("ODDS_API_KEY 沒有設定")

URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# 中文隊名對照
team_map = {
    "Milwaukee Bucks": "公鹿",
    "Orlando Magic": "魔術",
    "Boston Celtics": "塞爾提克",
    "Chicago Bulls": "公牛",
    "Cleveland Cavaliers": "騎士",
    "Washington Wizards": "巫師",
    "Philadelphia 76ers": "七六人",
    "New York Knicks": "尼克",
    "Brooklyn Nets": "籃網",
    "Indiana Pacers": "溜馬",
    "Toronto Raptors": "暴龍",
    "Detroit Pistons": "活塞",
    "Miami Heat": "熱火",
    "New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼",
    "Portland Trail Blazers": "拓荒者",
    "Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽",
    "Denver Nuggets": "金塊",
    "Memphis Grizzlies": "灰熊",
    "Los Angeles Clippers": "快艇",
    "Houston Rockets": "火箭",
    "Los Angeles Lakers": "湖人",
    "Golden State Warriors": "勇士",
    "Atlanta Hawks": "老鷹",
    "Charlotte Hornets": "黃蜂",
    "Sacramento Kings": "國王",
    "Utah Jazz": "爵士",
    "Dallas Mavericks": "獨行俠",
    "San Antonio Spurs": "馬刺"
}

def zh(name):
    return team_map.get(name, name)

def kelly(prob, odds=1.91):
    b = odds - 1
    return max((prob * b - (1 - prob)) / b, 0)

def analyze():
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    res = requests.get(URL, params=params)
    games = res.json()
    if not games:
        print("今天沒有比賽或 Odds API 無資料")
        return

    recommend_list = []
    normal_list = []

    for game in games:
        home = game["home_team"]
        away = game["away_team"]

        home_zh = zh(home)
        away_zh = zh(away)

        if not game.get("bookmakers"):
            continue

        for book in game["bookmakers"]:
            markets = book.get("markets", [])

            h2h = None
            spreads = None

            for m in markets:
                if m["key"] == "h2h":
                    h2h = m["outcomes"]
                if m["key"] == "spreads":
                    spreads = m["outcomes"]

            if not h2h or not spreads:
                continue

            # 勝率計算
            home_odds = next(o["price"] for o in h2h if o["name"] == home)
            away_odds = next(o["price"] for o in h2h if o["name"] == away)

            home_prob = 1 / home_odds
            away_prob = 1 / away_odds
            total = home_prob + away_prob
            home_prob /= total
            away_prob /= total

            home_k = kelly(home_prob)
            away_k = kelly(away_prob)

            # 讓分
            home_spread = next(o["point"] for o in spreads if o["name"] == home)
            away_spread = next(o["point"] for o in spreads if o["name"] == away)

            text = f"{away_zh} vs {home_zh}\n"
            text += f"主勝率：{home_prob:.2f}\n"
            text += f"讓分：{home_zh} {home_spread:+}\n"

            reco = ""

            # 勝負推薦（稍微放寬）
            if home_prob >= 0.63 and home_k >= 0.06:
                reco += f"🔴🔥 勝負：{home_zh} (Kelly {home_k:.2f})\n"
            elif home_prob <= 0.37 and away_k >= 0.06:
                reco += f"🔴🔥 勝負：{away_zh} (Kelly {away_k:.2f})\n"

            # 讓分推薦（稍微放寬）
            if home_prob >= 0.68 and home_spread <= -6:
                reco += f"🔴🔥 讓分：{home_zh} {home_spread:+}\n"
            elif home_prob <= 0.32 and away_spread >= 6:
                reco += f"🔴🔥 讓分：{away_zh} {away_spread:+}\n"

            if reco:
                recommend_list.append(text + reco + "\n")
            else:
                normal_list.append(text + "\n")
            break

    message = "**🔥推薦下注（職業模型V7 放寬版）**\n\n"
    message += "".join(recommend_list) if recommend_list else "今日無強勢推薦\n\n"
    message += "\n---\n\n**全部比賽**\n\n"
    message += "".join(normal_list)

    if WEBHOOK_URL:
        requests.post(WEBHOOK_URL, json={"content": message})
    else:
        print(message)

if __name__ == "__main__":
    analyze()