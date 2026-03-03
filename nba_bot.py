import requests
import os
import csv
import math
from datetime import datetime, timedelta

# ==============================
# 🛰️ V22.4 Elite Stability
# ==============================

STRICT_EDGE_BASE = 0.020
SPREAD_COEF = 0.16
DEEP_SPREAD_COEF = 0.13

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

CSV_FILE = "bet_history.csv"

# ==============================
# 中文隊名
# ==============================
TEAM_CN = {
    "Los Angeles Lakers": "湖人","Golden State Warriors": "勇士",
    "Boston Celtics": "塞爾提克","Milwaukee Bucks": "公鹿",
    "Denver Nuggets": "金塊","Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽","LA Clippers": "快艇",
    "Miami Heat": "熱火","Philadelphia 76ers": "七六人",
    "Sacramento Kings": "國王","New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼","Dallas Mavericks": "獨行俠",
    "New York Knicks": "尼克","Orlando Magic": "魔術",
    "Charlotte Hornets": "黃蜂","Detroit Pistons": "活塞",
    "Toronto Raptors": "暴龍","Chicago Bulls": "公牛",
    "San Antonio Spurs": "馬刺","Utah Jazz": "爵士",
    "Brooklyn Nets": "籃網","Atlanta Hawks": "老鷹",
    "Cleveland Cavaliers": "騎士","Indiana Pacers": "溜馬",
    "Memphis Grizzlies": "灰熊","Portland Trail Blazers": "拓荒者",
    "Washington Wizards": "巫師","Houston Rockets": "火箭"
}

def cn(name):
    return TEAM_CN.get(name, name)

# ==============================
# Kelly
# ==============================
def kelly(prob, odds, edge):
    if odds <= 1:
        return 0

    b = odds - 1
    raw = (prob * b - (1 - prob)) / b

    if edge >= 0.05:
        cap = 0.035
    elif edge >= 0.035:
        cap = 0.030
    else:
        cap = 0.025

    return min(max(0, raw), cap)

# ==============================
# 深盤懲罰
# ==============================
def spread_penalty(pt):
    abs_pt = abs(pt)

    if abs_pt <= 10:
        return abs_pt * 0.0028
    elif abs_pt <= 14:
        return abs_pt * 0.0035
    else:
        return (abs_pt * 0.0035) ** 1.12

# ==============================
# 記錄投注
# ==============================
def record_bet(date, game, market, pick, odds, edge, kelly_value):
    file_exists = os.path.isfile(CSV_FILE)

    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        if not file_exists:
            writer.writerow([
                "date","game","market",
                "pick","odds","edge",
                "kelly","result","profit"
            ])

        writer.writerow([
            date, game, market,
            pick, odds, round(edge,4),
            round(kelly_value,4), "pending", 0
        ])

# ==============================
# 30天績效計算
# ==============================
def calculate_performance():
    if not os.path.isfile(CSV_FILE):
        return 0,0,0,0

    cutoff = datetime.now() - timedelta(days=30)

    profits = []
    total_profit = 0
    total_stake = 0
    win_count = 0
    total_count = 0

    with open(CSV_FILE, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            if row["result"] == "pending":
                continue

            bet_date = datetime.strptime(row["date"], "%Y-%m-%d")
            if bet_date < cutoff:
                continue

            stake = float(row["kelly"])
            profit = float(row["profit"])

            if stake == 0:
                continue

            total_count += 1
            total_profit += profit
            total_stake += stake

            if profit > 0:
                win_count += 1

            profits.append(profit / stake)

    if total_count == 0:
        return 0,0,0,0

    win_rate = win_count / total_count
    roi = total_profit / total_stake if total_stake != 0 else 0

    avg_return = sum(profits) / len(profits)
    variance = sum((r - avg_return)**2 for r in profits) / len(profits)
    std_dev = math.sqrt(variance)

    sharpe = avg_return / std_dev if std_dev != 0 else 0

    return win_rate, roi, sharpe, std_dev

# ==============================
# 主程式
# ==============================
def main():

    try:
        res = requests.get(
            BASE_URL,
            params={
                "apiKey": API_KEY,
                "regions": "us",
                "markets": "h2h,spreads",
                "oddsFormat": "decimal"
            },
            timeout=10
        )

        if res.status_code != 200:
            return

        games = res.json()

    except:
        return

    today_str = datetime.now().strftime("%Y-%m-%d")

    for g in games:

        home = g["home_team"]
        away = g["away_team"]

        home_cn = cn(home)
        away_cn = cn(away)

        game_key = f"{away_cn} @ {home_cn}"

        markets = g.get("bookmakers", [{}])[0].get("markets", [])
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)

        if not h2h or not spreads:
            continue

        h_ml = next(o["price"] for o in h2h if o["name"] == home)
        a_ml = next(o["price"] for o in h2h if o["name"] == away)

        p_home = (1/h_ml)/((1/h_ml)+(1/a_ml))

        best_edge = -1
        best_data = None

        for o in spreads:
            pt = o["point"]
            odds = o["price"]
            is_home = o["name"] == home

            base_p = p_home if is_home else (1-p_home)
            bias = base_p - 0.5

            coef = DEEP_SPREAD_COEF if abs(pt)>12 else SPREAD_COEF
            p_spread = 0.5 + bias*coef + bias*0.06

            penalty = spread_penalty(pt)
            raw_diff = p_spread - (1/odds)
            edge = raw_diff*(1-penalty) if raw_diff>0 else raw_diff

            if edge > best_edge:
                team_cn = home_cn if is_home else away_cn
                best_edge = edge
                best_data = (team_cn, pt, odds, p_spread)

        if best_edge < STRICT_EDGE_BASE:
            continue

        team_cn, pt, odds, prob = best_data
        k = kelly(prob, odds, best_edge)

        if k <= 0:
            continue

        record_bet(
            today_str,
            game_key,
            "spread",
            f"{team_cn} {pt:+}",
            odds,
            best_edge,
            k
        )

    win_rate, roi, sharpe, volatility = calculate_performance()

    msg = "🛰️ V22.4 Elite Stability + Analytics\n\n"
    msg += f"30天 ROI: {roi:.2%}\n"
    msg += f"30天 勝率: {win_rate:.2%}\n"
    msg += f"Sharpe Ratio: {sharpe:.3f}\n"
    msg += f"報酬波動度: {volatility:.3f}"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
