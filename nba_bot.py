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

# ===== Discord =====
def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        requests.post(WEBHOOK_URL, json={"content": text[i:i+MAX]})

# ===== Kelly =====
def kelly(prob, odds=1.91):
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 3))

# ===== 模型修正 =====
def ema_adjust(p):
    if p > 0.6:
        p += 0.02
    elif p < 0.4:
        p -= 0.02
    return p

def home_adjust(p):
    return min(p + 0.03, 0.97)

def public_fade(p):
    if p > 0.75:
        p -= 0.03
    if p < 0.25:
        p += 0.03
    return p

# ===== 主程式 =====
def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "decimal"
    }

    res = requests.get(BASE_URL, params=params)
    games = res.json()

    candidates = []

    for g in games:
        home = g["home_team"]
        away = g["away_team"]

        market_probs = []

        # 多書平均
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

        if len(market_probs) == 0:
            continue

        market_avg = sum(market_probs) / len(market_probs)

        # ===== 模型機率 =====
        model_p = ema_adjust(market_avg)
        model_p = home_adjust(model_p)
        model_p = public_fade(model_p)

        # 用第一家當下注盤
        try:
            first_book = g["bookmakers"][0]["markets"][0]["outcomes"]
            home_ml = [o for o in first_book if o["name"] == home][0]["price"]
            away_ml = [o for o in first_book if o["name"] == away][0]["price"]
        except:
            continue

        book_p = (1/home_ml) / ((1/home_ml)+(1/away_ml))

        edge = model_p - book_p
        k = kelly(model_p)

        candidates.append({
            "game": f"{away} vs {home}",
            "model": model_p,
            "market": book_p,
            "edge": edge,
            "kelly": k
        })

    # ===== 沒有比賽 =====
    if not candidates:
        send_discord("今日沒有NBA賽事")
        return

    # ===== 按 Edge 排序 =====
    candidates.sort(key=lambda x: x["edge"], reverse=True)

    # ===== 取前2場 =====
    top_games = []
    for c in candidates:
        if c["kelly"] >= 0.01:
            top_games.append(c)
        if len(top_games) == 2:
            break

    if not top_games:
        send_discord("今日無可投注場次（Kelly過低）")
        return

    # ===== 發送 =====
    text = "**🔥今日最佳兩場（V10 Daily Top2）**\n"

    for c in top_games:
        text += f"\n{c['game']}\n"
        text += f"模型機率 {c['model']:.2f}\n"
        text += f"市場機率 {c['market']:.2f}\n"
        text += f"Edge {c['edge']:.3f}\n"
        text += f"Kelly {c['kelly']}\n"
        text += "🔴🔥 推薦下注\n"

    send_discord(text)

# ===== 執行 =====
if __name__ == "__main__":
    print("執行時間:", datetime.now())
    analyze()