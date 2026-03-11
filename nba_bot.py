import requests
import os
import math
from datetime import datetime, timedelta

# ==========================================
# NBA V150 AI Syndicate - Master Engine
# ==========================================

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

MIN_EV_THRESHOLD = 0.30   # 獲利門檻
MAX_PROB_CAP = 0.82       # 勝率封頂
HOME_ADV = 2.4            # 主場加成
DISPLAY_TIME_OFFSET = -10 # 顯示時間偏移
SPREAD_STD_BASE = 12.5    # 基礎波動率

# 節奏加成 (影響波動率)
PACE = {
    "Indiana Pacers":103, "Atlanta Hawks":101, "Golden State Warriors":101,
    "Oklahoma City Thunder":102, "Boston Celtics":100, "Denver Nuggets":97, "Miami Heat":96
}

# ... (TEAM_CN 與 TEAM_STATS 字典保持不變)

def normal_cdf(x, mean, std):
    """計算正態分佈累積分佈函數 (CDF)"""
    return 0.5 * (1 + math.erf((x - mean) / (std * math.sqrt(2))))

def predict_spread(home, away):
    h = TEAM_STATS.get(home, {"off":115, "def":115})
    a = TEAM_STATS.get(away, {"off":115, "def":115})
    rating = ((h["off"] - h["def"]) - (a["off"] - a["def"])) / 2
    return rating + HOME_ADV

def pace_factor(home, away):
    p1, p2 = PACE.get(home, 99), PACE.get(away, 99)
    return abs(p1 - p2) * 0.1

def calc_prob(model, line, pace):
    """使用解析解計算精確勝率"""
    diff = model - line
    # 動態標準差：基準 + 節奏影響 + 實力差距懲罰
    std = SPREAD_STD_BASE + pace + abs(model) * 0.4
    
    # 計算 P(X > 0)
    prob = 1 - normal_cdf(0, diff, std)
    
    # 深盤保護：讓分過深時自動衰減勝率
    if abs(line) > 10:
        prob *= 0.95
        
    return max(1 - MAX_PROB_CAP, min(prob, MAX_PROB_CAP))

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    r = requests.get(BASE_URL, params={
        "apiKey": API_KEY, "regions": "us", "markets": "spreads", "oddsFormat": "decimal"
    })
    
    if r.status_code != 200: return
    games = r.json()
    grouped = {}

    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence_tw = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        if now_tw > (commence_tw + timedelta(minutes=150)): continue

        display_tw = commence_tw + timedelta(minutes=DISPLAY_TIME_OFFSET)
        date_key = commence_tw.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")

        model_spread = predict_spread(home, away)
        pace = pace_factor(home, away)

        # 1. 計算市場平均盤口 (V90 核心邏輯補回)
        market_lines = []
        for book in g.get("bookmakers", []):
            for m in book["markets"]:
                if m["key"] == "spreads":
                    for o in m["outcomes"]:
                        if o["name"] == home: market_lines.append(o["point"])
        
        if not market_lines: continue
        avg_market_line = sum(market_lines) / len(market_lines)

        # 2. V150 雙重過濾：線差必須 > 1.5 且 盤口不能 > 14
        if abs(model_spread - avg_market_line) < 1.5: continue
        if abs(avg_market_line) > 14: continue

        best_pick = None
        for book in g.get("bookmakers", []):
            for m in book["markets"]:
                if m["key"] != "spreads": continue
                for o in m["outcomes"]:
                    target = model_spread if o["name"] == home else -model_spread
                    prob = calc_prob(target, o["point"], pace)
                    
                    odds = o["price"]
                    ev = prob * (odds - 1) - (1 - prob)
                    edge = prob - (1/odds)

                    if ev >= MIN_EV_THRESHOLD:
                        pick = {
                            "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                            "team": TEAM_CN.get(o["name"], o["name"]),
                            "label": "[受讓]" if o["point"] > 0 else "[讓分]",
                            "point": f"{o['point']:+}", "odds": odds,
                            "ev": ev, "prob": prob, "implied": 1/odds, "edge": edge,
                            "time": display_tw.strftime("%H:%M")
                        }
                        if not best_pick or ev > best_pick["ev"]: best_pick = pick

        if best_pick:
            grouped.setdefault(date_key, []).append(best_pick)

    # 輸出訊息
    message = f"🛡️ **NBA V150 AI Syndicate**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    message += f"🚫 已過濾 EV < {MIN_EV_THRESHOLD} | 深盤 > 14 | 線差 < 1.5\n"

    if not grouped:
        message += "\n📭 目前無符合條件場次"
    else:
        for date, picks in grouped.items():
            message += f"\n📅 **{date}**\n"
            picks.sort(key=lambda x: x["ev"], reverse=True)
            day_parlay = []
            for p in picks:
                day_parlay.append(p)
                message += f"**{p['match']}** {p['label']}\n"
                message += f"✨ ⏰ {p['time']} | 🎯 {p['team']} {p['point']} @ **{p['odds']}**\n"
                message += f"💰 EV: **+{p['ev']:.2f}** | 📈 Edge: **{p['edge']:+.2%}**\n"
                message += f"📊 勝率: 模型 **{p['prob']:.1%}** vs 市場 **{p['implied']:.1%}**\n"
                message += "--------\n"

            if len(day_parlay) >= 2:
                top = day_parlay[:2]
                message += f"💎 **{date} AI 推薦串關 (組合賠率 {(top[0]['odds']*top[1]['odds']):.2f})**\n"
                message += f"✅ {top[0]['match']} {top[0]['label']}\n✅ {top[1]['match']} {top[1]['label']}\n"
                message += "================\n"

    requests.post(WEBHOOK, json={"content": message})

if __name__ == "__main__":
    run()
