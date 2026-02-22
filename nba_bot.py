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
    "LA Clippers": "快艇",
    "Golden State Warriors": "勇士",
    "Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿",
    "Denver Nuggets": "金塊",
    "Phoenix Suns": "太陽",
    "Miami Heat": "熱火",
    "Philadelphia 76ers": "七六人",
    "Dallas Mavericks": "獨行俠",
    "Sacramento Kings": "國王",
    "Minnesota Timberwolves": "灰狼",
    "New York Knicks": "尼克",
    "Cleveland Cavaliers": "騎士",
    "Memphis Grizzlies": "灰熊",
    "Chicago Bulls": "公牛",
    "Toronto Raptors": "暴龍",
    "Houston Rockets": "火箭",
    "Oklahoma City Thunder": "雷霆",
    "Atlanta Hawks": "老鷹",
    "Indiana Pacers": "溜馬",
    "Brooklyn Nets": "籃網",
    "Utah Jazz": "爵士",
    "San Antonio Spurs": "馬刺",
    "Orlando Magic": "魔術",
    "Charlotte Hornets": "黃蜂",
    "Detroit Pistons": "活塞",
    "Washington Wizards": "巫師",
    "Portland Trail Blazers": "拓荒者",
    "New Orleans Pelicans": "鵜鶘"
}

def cn(team):
    return TEAM_CN.get(team, team)

# ===== Discord 發送 =====
def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        requests.post(WEBHOOK_URL, json={"content": text[i:i+MAX]})

# ===== Kelly =====
def kelly(prob, odds=1.91):
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 3))

# ===== 模型微調 =====
def adjust_model(p):
    if p > 0.6:
        p += 0.02
    elif p < 0.4:
        p -= 0.02
    if p > 0.75:
        p -= 0.03
    if p < 0.25:
        p += 0.03
    return min(max(p, 0.05), 0.95)

# ===== 主分析 =====
def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    res = requests.get(BASE_URL, params=params)
    games = res.json()
    best_per_game = []

    for g in games:
        home = g["home_team"]
        away = g["away_team"]
        best_pick = None

        for book in g["bookmakers"]:
            h2h = None
            spreads = None
            for m in book["markets"]:
                if m["key"] == "h2h":
                    h2h = m["outcomes"]
                elif m["key"] == "spreads":
                    spreads = m["outcomes"]
            if not h2h:
                continue

            try:
                home_ml = [o for o in h2h if o["name"] == home][0]["price"]
                away_ml = [o for o in h2h if o["name"] == away][0]["price"]
            except:
                continue

            # ===== Moneyline =====
            p_market = (1/home_ml) / ((1/home_ml)+(1/away_ml))
            p_model = adjust_model(p_market)
            edge_ml = round(p_model - p_market, 3)
            k_ml = round(kelly(p_model),3)

            best_pick = {
                "game": f"{cn(away)} vs {cn(home)}",
                "type": "不讓分",
                "pick": cn(home) if p_model > 0.5 else cn(away),
                "edge": edge_ml,
                "kelly": k_ml
            }

            # ===== 保守讓分（3~6 分） =====
            if spreads:
                try:
                    spread_home = [o for o in spreads if o["name"] == home][0]
                    spread_point = spread_home["point"]
                    if 3 <= abs(spread_point) <= 6:
                        spread_prob = p_model - (spread_point * 0.006)
                        spread_prob = min(max(spread_prob, 0.1), 0.9)
                        edge_sp = round(spread_prob - 0.5,3)
                        k_sp = round(kelly(spread_prob),3)
                        if edge_sp > edge_ml and edge_sp > 0.05 and k_sp > 0.05:
                            best_pick = {
                                "game": f"{cn(away)} vs {cn(home)}",
                                "type": f"讓分 {spread_point:+}",
                                "pick": cn(home) if spread_prob > 0.5 else cn(away),
                                "edge": edge_sp,
                                "kelly": k_sp
                            }
                except:
                    pass

        if best_pick:
            best_per_game.append(best_pick)

    if not best_per_game:
        send_discord("今日沒有NBA賽事")
        return

    # ===== 前兩場最佳 =====
    best_per_game.sort(key=lambda x: x["edge"], reverse=True)
    top2 = best_per_game[:2]

    text = "**🔥今日最佳兩場（V10.5 超保守）**\n"
    for c in top2:
        text += f"\n{c['game']}\n"
        text += f"玩法：{c['type']}\n"
        text += f"推薦：{c['pick']}\n"
        text += f"Edge：{c['edge']:.3f}\n"
        text += f"Kelly：{c['kelly']:.3f}\n"

    send_discord(text)

# ===== 執行 =====
if __name__ == "__main__":
    print("執行時間:", datetime.now())
    analyze()