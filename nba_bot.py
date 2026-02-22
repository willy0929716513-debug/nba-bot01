import requests
import os
from datetime import datetime, timedelta

# ===== 環境變數 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# ===== 核心參數：這是穩定贏錢的關鍵 =====
MIN_EDGE = 0.03       # 只有優勢大於 3% 的場次才推薦
KELLY_FRACTION = 0.1  # 僅下注凱利建議的 10%，極度保守以應對波動

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

TEAM_CN = {
    "Los Angeles Lakers": "湖人", "LA Clippers": "快艇", "Golden State Warriors": "勇士",
    "Boston Celtics": "塞爾提克", "Milwaukee Bucks": "公鹿", "Denver Nuggets": "金塊",
    "Phoenix Suns": "太陽", "Miami Heat": "熱火", "Philadelphia 76ers": "七六人",
    "Dallas Mavericks": "獨行俠", "Sacramento Kings": "國王", "Minnesota Timberwolves": "灰狼",
    "New York Knicks": "尼克", "Cleveland Cavaliers": "騎士", "Memphis Grizzlies": "灰熊",
    "Chicago Bulls": "公牛", "Toronto Raptors": "暴龍", "Houston Rockets": "火箭",
    "Oklahoma City Thunder": "雷霆", "Atlanta Hawks": "老鷹", "Indiana Pacers": "溜馬",
    "Brooklyn Nets": "籃網", "Utah Jazz": "爵士", "San Antonio Spurs": "馬刺",
    "Orlando Magic": "魔術", "Charlotte Hornets": "黃蜂", "Detroit Pistons": "活塞",
    "Washington Wizards": "巫師", "Portland Trail Blazers": "拓荒者", "New Orleans Pelicans": "鵜鶘"
}

def cn(team): return TEAM_CN.get(team, team)

def get_no_vig_prob(h2h_outcomes):
    try:
        inv_sum = sum(1/o["price"] for o in h2h_outcomes)
        return {o["name"]: (1/o["price"]) / inv_sum for o in h2h_outcomes}
    except: return None

def kelly_criterion(prob, odds, fraction=KELLY_FRACTION):
    if prob <= (1/odds): return 0
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return round(k * fraction, 4)

def estimate_spread_prob(win_prob, spread):
    # NBA 分數分佈模型：1分約等於 2.8% 勝率
    adjustment = spread * 0.028 
    return min(max(win_prob + adjustment, 0.05), 0.95)

def format_pick(team, point=None):
    if point is None: return f"{cn(team)} (主勝)"
    return f"{cn(team)} {point:+g}"

def analyze():
    params = {"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"}
    try:
        res = requests.get(BASE_URL, params=params).json()
    except: return

    recommendations = []

    for g in res:
        # 時間過濾：確保是未來場次
        commence_time = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        if commence_time < datetime.now(commence_time.tzinfo): continue

        home, away = g["home_team"], g["away_team"]
        
        # 1. 聚合市場數據
        best_h2h = {home: 0, away: 0}
        best_sp = {home: {"p": 0, "o": 0}, away: {"p": 0, "o": 0}}
        all_probs = []

        for book in g.get("bookmakers", []):
            for m in book.get("markets", []):
                if m["key"] == "h2h":
                    probs = get_no_vig_prob(m["outcomes"])
                    if probs: all_probs.append(probs)
                    for o in m["outcomes"]:
                        best_h2h[o["name"]] = max(best_h2h[o["name"]], o["price"])
                
                if m["key"] == "spreads":
                    for o in m["outcomes"]:
                        best_sp[o["name"]]["p"] = o["point"]
                        best_sp[o["name"]]["o"] = max(best_sp[o["name"]]["o"], o["price"])

        if not all_probs: continue
        avg_p_home = sum(p[home] for p in all_probs) / len(all_probs)

        # 2. 評估不讓分 (Moneyline)
        for t in [home, away]:
            p = avg_p_home if t == home else (1 - avg_p_home)
            odds = best_h2h[t]
            edge = p - (1/odds)
            k = kelly_criterion(p, odds)
            if edge >= MIN_EDGE and k > 0:
                recommendations.append({"game": f"{cn(away)} @ {cn(home)}", "pick": format_pick(t), "odds": odds, "edge": edge, "k": k})

        # 3. 評估讓分盤 (Spreads)
        if best_sp[home]["o"] > 0:
            p_h_sp = estimate_spread_prob(avg_p_home, best_sp[home]["p"])
            # 主隊讓球
            edge_h = p_h_sp - (1/best_sp[home]["o"])
            k_h = kelly_criterion(p_h_sp, best_sp[home]["o"])
            if edge_h >= MIN_EDGE and k_h > 0:
                recommendations.append({"game": f"{cn(away)} @ {cn(home)}", "pick": format_pick(home, best_sp[home]["p"]), "odds": best_sp[home]["o"], "edge": edge_h, "k": k_h})
            # 客隊受讓
            edge_a = (1-p_h_sp) - (1/best_sp[away]["o"])
            k_a = kelly_criterion(1-p_h_sp, best_sp[away]["o"])
            if edge_a >= MIN_EDGE and k_a > 0:
                recommendations.append({"game": f"{cn(away)} @ {cn(home)}", "pick": format_pick(away, best_sp[away]["p"]), "odds": best_sp[away]["o"], "edge": edge_a, "k": k_a})

    # 4. 發送所有符合標的的推薦
    if not recommendations:
        send_discord("🔍 當前市場賠率平穩，無具備優勢(Edge)的價值標的。穩定第一，今日建議觀望。")
        return

    recommendations.sort(key=lambda x: x["edge"], reverse=True)
    msg = f"📈 **NBA 價值投資推薦 (Edge > {MIN_EDGE:.0%})**\n"
    for r in recommendations:
        msg += f"\n🏀 **{r['game']}**\n推薦：`{r['pick']}`\n最佳賠率：`{r['odds']}` | 優勢：`{r['edge']:.1%}`\n建議水位：`{r['k']:.1%}` 倉位\n"
    
    send_discord(msg)

def send_discord(text):
    requests.post(WEBHOOK_URL, json={"content": text})

if __name__ == "__main__":
    analyze()
