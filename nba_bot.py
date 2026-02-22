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

def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        requests.post(WEBHOOK_URL, json={"content": text[i:i+MAX]})

def kelly(prob, odds=1.91):
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 3))

def adjust_model(p):
    # 簡單修正
    if p > 0.6:
        p += 0.02
    elif p < 0.4:
        p -= 0.02

    if p > 0.75:
        p -= 0.03
    if p < 0.25:
        p += 0.03

    return min(max(p, 0.05), 0.95)

def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    res = requests.get(BASE_URL, params=params)
    games = res.json()

    candidates = []

    for g in games:
        home = g["home_team"]
        away = g["away_team"]

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
            p_home = (1/home_ml) / ((1/home_ml)+(1/away_ml))
            model_p = adjust_model(p_home)

            edge_ml = model_p - p_home
            k_ml = kelly(model_p)

            candidates.append({
                "game": f"{away} vs {home}",
                "type": "不讓分",
                "pick": home,
                "edge": edge_ml,
                "kelly": k_ml
            })

            # ===== Spread =====
            if spreads:
                try:
                    spread_home = [o for o in spreads if o["name"] == home][0]
                    spread_point = spread_home["point"]
                    spread_price = spread_home["price"]

                    # 簡單讓分模型（依勝率推估）
                    spread_prob = model_p - (spread_point * 0.015)
                    spread_prob = min(max(spread_prob, 0.05), 0.95)

                    edge_sp = spread_prob - 0.5
                    k_sp = kelly(spread_prob)

                    candidates.append({
                        "game": f"{away} vs {home}",
                        "type": f"讓分 {spread_point:+}",
                        "pick": home,
                        "edge": edge_sp,
                        "kelly": k_sp
                    })
                except:
                    pass

    if not candidates:
        send_discord("今日沒有NBA賽事")
        return

    # 排序
    candidates.sort(key=lambda x: x["edge"], reverse=True)

    top2 = candidates[:2]

    text = "**🔥今日最佳兩場（含讓分）**\n"

    for c in top2:
        text += f"\n{c['game']}\n"
        text += f"玩法：{c['type']}\n"
        text += f"推薦：{c['pick']}\n"
        text += f"Edge：{c['edge']:.3f}\n"
        text += f"Kelly：{c['kelly']}\n"

    send_discord(text)

if __name__ == "__main__":
    print("執行時間:", datetime.now())
    analyze()