import requests
import os
from datetime import datetime

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

if not API_KEY:
    raise ValueError("ODDS_API_KEY 沒有設定")

if not WEBHOOK_URL:
    raise ValueError("DISCORD_WEBHOOK 沒有設定")

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

TEAM_CN = {
    "Los Angeles Lakers": "湖人",
    "Golden State Warriors": "勇士",
    "Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿",
    "Denver Nuggets": "金塊",
    "Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽",
    "LA Clippers": "快艇",
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
    "Portland Trail Blazers": "拓荒者",
    "Washington Wizards": "巫師",
    "Houston Rockets": "火箭"
}

def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        requests.post(WEBHOOK_URL, json={"content": text[i:i+MAX]})

def kelly(prob, odds=1.91):
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 3))

def sharp_model(market_prob):
    """
    Sharp 模型：
    - 過熱熱門削弱
    - 被低估冷門提升
    """
    if market_prob > 0.75:
        market_prob -= 0.06
    elif market_prob > 0.65:
        market_prob -= 0.03
    elif market_prob < 0.30:
        market_prob += 0.05

    return min(max(market_prob, 0.05), 0.95)

def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    res = requests.get(BASE_URL, params=params)
    games = res.json()

    text = "**🔥推薦下注（V9 Sharp 模型）**\n"
    rec_count = 0

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
        spreads = None

        for m in markets:
            if m["key"] == "h2h":
                h2h = m["outcomes"]
            elif m["key"] == "spreads":
                spreads = m["outcomes"]

        if not h2h:
            continue

        home_ml = [o for o in h2h if o["name"] == home_en][0]["price"]
        away_ml = [o for o in h2h if o["name"] == away_en][0]["price"]

        market_home = (1/home_ml) / ((1/home_ml)+(1/away_ml))
        model_home = sharp_model(market_home)

        value_home = model_home - market_home
        value_away = (1-model_home) - (1-market_home)

        k_home = min(kelly(model_home), 0.18)
        k_away = min(kelly(1-model_home), 0.18)

        spread_val = None
        spread_text = ""
        if spreads:
            home_spread = [o for o in spreads if o["name"] == home_en][0]["point"]
            spread_val = home_spread
            spread_text = f"{home} {home_spread:+}"

        recs = []
        signal = 0

        # ===== Value條件（≥5%）=====
        if value_home >= 0.05 and k_home > 0.04:
            recs.append(f"🔴🔥 勝負：{home} (Value {value_home:.2f}, Kelly {k_home})")
            signal += 1
        elif value_away >= 0.05 and k_away > 0.04:
            recs.append(f"🔴🔥 勝負：{away} (Value {value_away:.2f}, Kelly {k_away})")
            signal += 1

        # ===== 讓分確認（避免大熱門陷阱）=====
        if spread_val is not None:
            if 2.5 <= abs(spread_val) <= 6:
                if model_home > 0.68:
                    recs.append(f"🔴🔥 讓分：{home} {spread_val:+}")
                    signal += 1
                elif model_home < 0.32:
                    recs.append(f"🔴🔥 讓分：{away} {-spread_val:+}")
                    signal += 1

        # 至少兩訊號
        if signal >= 2:
            rec_count += 1
            text += f"\n{away} vs {home}\n"
            text += f"市場主勝率：{market_home:.2f}\n"
            text += f"模型主勝率：{model_home:.2f}\n"
            text += f"讓分：{spread_text}\n"
            for r in recs:
                text += r + "\n"

    if rec_count == 0:
        text += "\n今日無明顯Sharp機會"

    send_discord(text)

if __name__ == "__main__":
    print("執行時間:", datetime.now())
    analyze()
