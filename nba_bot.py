import requests
import os
from datetime import datetime

# ===== 環境變數 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

if not API_KEY:
    raise ValueError("ODDS_API_KEY 沒有設定")

if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL 沒有設定")

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# ===== 中文隊名 =====
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

# ===== Discord分段 =====
def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        part = text[i:i+MAX]
        r = requests.post(WEBHOOK_URL, json={"content": part})
        if r.status_code != 200:
            print("Discord 發送失敗:", r.status_code, r.text)

# ===== Kelly公式 =====
def kelly(prob, odds=1.91):
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 3))

# ===== EMA實力（模擬近況）=====
def ema_power(prob):
    # 模擬近期狀態：向極端靠一點
    if prob > 0.6:
        return prob + 0.03
    elif prob < 0.4:
        return prob - 0.03
    return prob

# ===== 主場加權 =====
def home_adjust(prob):
    return min(prob + 0.03, 0.97)

# ===== 主程式 =====
def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    res = requests.get(BASE_URL, params=params)
    games = res.json()

    recommend_text = "**🔥推薦下注（職業模型V7 精準版）**\n"
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
        spreads = None

        for m in markets:
            if m["key"] == "h2h":
                h2h = m["outcomes"]
            elif m["key"] == "spreads":
                spreads = m["outcomes"]

        if not h2h:
            continue

        # ===== 市場機率 =====
        home_ml = [o for o in h2h if o["name"] == home_en][0]["price"]
        away_ml = [o for o in h2h if o["name"] == away_en][0]["price"]

        p_home = (1/home_ml) / ((1/home_ml)+(1/away_ml))

        # ===== EMA + 主場 =====
        p_home = ema_power(p_home)
        p_home = home_adjust(p_home)

        k_home = kelly(p_home)
        k_away = kelly(1 - p_home)

        # ===== 讓分 =====
        spread_text = ""
        spread_val = None
        if spreads:
            home_spread = [o for o in spreads if o["name"] == home_en][0]["point"]
            spread_val = home_spread
            spread_text = f"{home} {home_spread:+}"

        # ===== 顯示內容 =====
        game_info = f"\n{away} vs {home}\n"
        game_info += f"主勝率：{p_home:.2f}\n"
        game_info += f"讓分：{spread_text}\n"

        recs = []
        signal_count = 0

        # ===== 勝負推薦（嚴格）=====
        if p_home > 0.67 and k_home > 0.05:
            recs.append(f"🔴🔥 勝負：{home} (Kelly {k_home})")
            signal_count += 1
        elif p_home < 0.33 and k_away > 0.05:
            recs.append(f"🔴🔥 勝負：{away} (Kelly {k_away})")
            signal_count += 1

        # ===== 讓分推薦（合理範圍）=====
        if spread_val is not None:
            if 3 <= abs(spread_val) <= 9:
                if p_home > 0.70:
                    recs.append(f"🔴🔥 讓分：{home} {spread_val:+}")
                    signal_count += 1
                elif p_home < 0.30:
                    recs.append(f"🔴🔥 讓分：{away} {-spread_val:+}")
                    signal_count += 1

        # ===== 至少1個訊號才推薦 =====
        if signal_count >= 1:
            recommend_text += game_info
            for r in recs:
                recommend_text += r + "\n"

        # 全部比賽
        all_text += game_info
        for r in recs:
            all_text += r + "\n"

    send_discord(recommend_text)
    send_discord(all_text)

# ===== 執行 =====
if __name__ == "__main__":
    print("執行時間:", datetime.now())
    analyze()