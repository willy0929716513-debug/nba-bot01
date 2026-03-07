import requests
import os
import json
import random
from datetime import datetime, timedelta

# ==========================================
# NBA V50.1 AI PRO ENGINE (Adaptive ELO)
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
DB_FILE = "nba_v50_db.json"

SIMULATIONS = 10000
EDGE_THRESHOLD = 0.025
HOME_ADV = 2.5
CLV_DANGER_ZONE = -0.05

# 初始 ELO 設定 (已對齊 API 名稱)
INITIAL_ELO = {
    "Boston Celtics": 1680, "Denver Nuggets": 1655, "Oklahoma City Thunder": 1640,
    "Milwaukee Bucks": 1625, "Minnesota Timberwolves": 1610, "Los Angeles Clippers": 1590,
    "Dallas Mavericks": 1585, "Phoenix Suns": 1580, "Golden State Warriors": 1575,
    "Los Angeles Lakers": 1565, "New York Knicks": 1560, "Cleveland Cavaliers": 1555,
    "Philadelphia 76ers": 1545, "Sacramento Kings": 1535, "Miami Heat": 1520,
    "Indiana Pacers": 1510, "Houston Rockets": 1505, "New Orleans Pelicans": 1500,
    "Atlanta Hawks": 1495, "Chicago Bulls": 1490, "Toronto Raptors": 1480,
    "Brooklyn Nets": 1470, "Charlotte Hornets": 1460, "Detroit Pistons": 1450,
    "Utah Jazz": 1445, "Portland Trail Blazers": 1440, "San Antonio Spurs": 1435,
    "Washington Wizards": 1425, "Memphis Grizzlies": 1500, "Orlando Magic": 1515
}

TEAM_CN = {
    "Los Angeles Lakers": "湖人", "Golden State Warriors": "勇士", "Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿", "Denver Nuggets": "金塊", "Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽", "Los Angeles Clippers": "快艇", "Miami Heat": "熱火",
    "Philadelphia 76ers": "七六人", "Sacramento Kings": "國王", "New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼", "Dallas Mavericks": "獨行俠", "New York Knicks": "尼克",
    "Orlando Magic": "魔術", "Charlotte Hornets": "黃蜂", "Detroit Pistons": "活塞",
    "Toronto Raptors": "暴龍", "Chicago Bulls": "公牛", "San Antonio Spurs": "馬刺",
    "Utah Jazz": "爵士", "Brooklyn Nets": "籃網", "Atlanta Hawks": "老鷹",
    "Cleveland Cavaliers": "騎士", "Indiana Pacers": "溜馬", "Memphis Grizzlies": "灰熊",
    "Portland Trail Blazers": "拓荒者", "Washington Wizards": "巫師", "Houston Rockets": "火箭"
}

# ==========================================
# 核心函數
# ==========================================

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: return json.load(f)
        except: pass
    return {"elo": INITIAL_ELO, "history": {}}

def save_db(db):
    with open(DB_FILE, "w") as f: json.dump(db, f, indent=2)

def elo_spread(home, away, elo):
    h = elo.get(home, 1500)
    a = elo.get(away, 1500)
    return ((h - a) / 28) + HOME_ADV

def simulate(model_spread, market_point):
    wins = sum(1 for _ in range(SIMULATIONS) if random.gauss(model_spread, 13.5) + market_point > 0)
    return wins / SIMULATIONS

def calculate_kelly(prob, odds, is_danger=False):
    b, q = odds - 1, 1 - prob
    k = (b * prob - q) / b if b != 0 else 0
    # 危險區減碼 50 倍 (0.5%)，正常區最高 3%
    mult = 0.005 if is_danger else 0.25
    limit = 0.005 if is_danger else 0.03
    return min(max(0, k * mult), limit)

# ==========================================
# 引擎執行
# ==========================================

def run():
    db = load_db()
    elo, history = db["elo"], db["history"]
    now_tw = datetime.utcnow() + timedelta(hours=8)

    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions": "us", "markets": "spreads", "oddsFormat": "decimal"}, timeout=15)
        games = res.json()
    except: return

    message = f"🧠 **NBA V50.1 AI Pro Engine**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    picks = 0

    for g in games:
        home, away, gid = g["home_team"], g["away_team"], g["id"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        is_live = now_tw > (commence_tw - timedelta(minutes=2))
        
        books = g.get("bookmakers", [])
        if not books: continue
        market = next((m["outcomes"] for m in books[0]["markets"] if m["key"] == "spreads"), None)
        if not market: continue

        model_line = elo_spread(home, away, elo)
        best = None

        for o in market:
            team, point, odds = o["name"], o["point"], o["price"]
            
            # --- ELO 動態學習邏輯 ---
            if gid not in history:
                history[gid] = {"open": odds, "team": team}
            
            clv = (odds - history[gid]["open"]) / history[gid]["open"]
            if abs(clv) > 0.01:
                # 根據 CLV 大小決定 ELO 調整強度
                step = 5 if abs(clv) < 0.04 else (12 if abs(clv) < 0.08 else 25)
                elo[team] = elo.get(team, 1500) + (step if clv > 0 else -step)

            # 蒙地卡羅模擬 (針對當前選項計算勝率)
            # 如果選項名稱是主隊，則 market_point 就是盤分；若為客隊，盤分要取反
            is_home = (team == home)
            prob = simulate(model_line, point if is_home else -point)
            edge = prob - (1/odds)

            if best is None or edge > best["edge"]:
                best = {"team": team, "point": point, "odds": odds, "prob": prob, "edge": edge, "clv": clv}

        if best and best["edge"] > EDGE_THRESHOLD:
            picks += 1
            is_danger = best["clv"] <= CLV_DANGER_ZONE
            ev = (best["prob"] * (best["odds"] - 1)) - (1 - best["prob"])
            stake = calculate_kelly(best["prob"], best["odds"], is_danger)
            
            grade = "⚠️ 數據異常" if is_danger else ("🔥 S級" if best["edge"] > 0.05 else "⭐ A級")
            live_tag = "🔴 [場中] " if is_live else ""
            
            message += f"\n{live_tag}{grade} **{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}**\n"
            message += f"🎯 {TEAM_CN.get(best['team'],best['team'])} {best['point']:+}\n"
            message += f"Edge: **{best['edge']:.2%}** | EV: **{ev:.2%}**\n"
            message += f"CLV: {'📈' if best['clv']>0 else '📉'}{best['clv']:.2%} | Kelly: **{stake:.2%}**\n"
            message += "---------\n"

    if picks == 0:
        message += "\n📭 今日無符合條件推薦"

    requests.post(WEBHOOK, json={"content": message})
    save_db({"elo": elo, "history": history})

if __name__ == "__main__":
    run()
