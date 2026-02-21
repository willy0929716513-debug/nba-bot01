import requests
import os
import json
from datetime import datetime

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

if not API_KEY:
    raise ValueError("ODDS_API_KEY 沒有設定")

if not WEBHOOK_URL:
    raise ValueError("DISCORD_WEBHOOK 沒有設定")

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
DATA_FILE = "line_history.json"

def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        requests.post(WEBHOOK_URL, json={"content": text[i:i+MAX]})

def kelly(prob, odds=1.91):
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 3))

def sharp_adjust(p):
    if p > 0.75:
        p -= 0.05
    elif p < 0.30:
        p += 0.05
    return min(max(p, 0.05), 0.95)

def load_history():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_history(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    res = requests.get(BASE_URL, params=params)
    games = res.json()

    history = load_history()
    new_history = {}

    text = "**🔥推薦下注（V11 Sharp Money 模型）**\n"
    rec_count = 0

    for g in games:
        game_id = g["id"]
        home = g["home_team"]
        away = g["away_team"]

        market_probs = []

        for book in g["bookmakers"]:
            for m in book["markets"]:
                if m["key"] == "h2h":
                    outcomes = m["outcomes"]
                    try:
                        home_ml = [o for o in outcomes if o["name"] == home][0]["price"]
                        away_ml = [o for o in outcomes if o["name"] == away][0]["price"]
                        p_home = (1/home_ml) / ((1/home_ml)+(1/away_ml))
                        market_probs.append(p_home)
                    except:
                        continue

        if len(market_probs) < 2:
            continue

        avg_market = sum(market_probs) / len(market_probs)
        model_prob = sharp_adjust(avg_market)

        first_book = g["bookmakers"][0]
        h2h = None
        spreads = None

        for m in first_book["markets"]:
            if m["key"] == "h2h":
                h2h = m["outcomes"]
            elif m["key"] == "spreads":
                spreads = m["outcomes"]

        if not h2h:
            continue

        home_ml = [o for o in h2h if o["name"] == home][0]["price"]
        away_ml = [o for o in h2h if o["name"] == away][0]["price"]
        book_prob = (1/home_ml) / ((1/home_ml)+(1/away_ml))

        spread_val = None
        if spreads:
            spread_val = [o for o in spreads if o["name"] == home][0]["point"]

        # ===== 存當前盤口 =====
        new_history[game_id] = {
            "prob": book_prob,
            "spread": spread_val
        }

        recs = []
        signal = 0

        # ===== 價值訊號 =====
        edge = model_prob - book_prob
        k = min(kelly(model_prob), 0.12)

        if edge >= 0.06 and k > 0.03:
            recs.append(f"🔴🔥 勝負 Edge {edge:.2f} Kelly {k}")
            signal += 1

        # ===== Line Movement 偵測 =====
        if game_id in history:
            old_prob = history[game_id]["prob"]
            movement = book_prob - old_prob

            # Reverse Line Movement
            if movement < -0.03 and model_prob > avg_market:
                recs.append("🔥 Reverse Line Movement 偵測")
                signal += 1

        if signal >= 2:
            rec_count += 1
            text += f"\n{away} vs {home}\n"
            text += f"市場均值 {avg_market:.2f}\n"
            text += f"模型機率 {model_prob:.2f}\n"
            for r in recs:
                text += r + "\n"

    save_history(new_history)

    if rec_count == 0:
        text += "\n今日無Sharp資金訊號"

    send_discord(text)

if __name__ == "__main__":
    print("執行時間:", datetime.now())
    analyze()