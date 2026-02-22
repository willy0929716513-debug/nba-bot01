import requests
import os
from datetime import datetime, timedelta

# ===== 環境變數設定 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# ===== 平衡型參數設定 =====
MIN_EDGE = 0.015      # 放寬至 1.5%，增加場次但仍保有正期望值
KELLY_FRACTION = 0.08 # 稍微降低凱利比例（8%），應對增加的波動性，確保不虧大錢
MAX_ODDS = 3.5        # 避開冷門大賠率，那類場次波動太大，不利於穩定獲利
MIN_ODDS = 1.3        # 避開過熱賠率，這種場次通常沒有肉

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
    # 採用 2.8% 模型，這在 NBA 現代數據中較為穩健
    adjustment = spread * 0.028 
    return min(max(win_prob + adjustment, 0.05), 0.95)

def format_pick(team, point=None):
    if point is None: return f"{cn(team)} (不讓分)"
    return f"{cn(team)} {point:+g}"

def analyze():
    params = {"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"}
    try:
        res = requests.get(BASE_URL, params=params).json()
    except Exception as e:
        print(f"API Error: {e}")
        return

    recommendations = []

    for g in res:
        commence_time = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        if commence_time < datetime.now(commence_time.tzinfo): continue

        home, away = g["home_team"], g["away_team"]
        
        # 1. 抓取各家最佳賠率
        best_h2h = {home: 0, away: 0}
        best_sp = {home: {"p": 0, "o": 0}, away: {"p": 0, "o": 0}}
        all_market_probs = []

        for book in g.get("bookmakers", []):
            for m in book.get("markets", []):
                if m["key"] == "h2h":
                    p_dict = get_no_vig_prob(m["outcomes"])
                    if p_dict: all_market_probs.append(p_dict)
                    for o in m["outcomes"]:
                        best_h2h[o["name"]] = max(best_h2h[o["name"]], o["price"])
                
                if m["key"] == "spreads":
                    for o in m["outcomes"]:
                        # 記錄該方向的最佳賠率
                        if o["price"] > best_sp[o["name"]]["o"]:
                            best_sp[o["name"]]["p"] = o["point"]
                            best_sp[o["name"]]["o"] = o["price"]

        if not all_market_probs: continue
        avg_p_home = sum(p[home] for p in all_market_probs) / len(all_market_probs)

        # 2. 評估不讓分
        for t in [home, away]:
            prob = avg_p_home if t == home else (1 - avg_p_home)
            odds = best_h2h[t]
            if MIN_ODDS <= odds <= MAX_ODDS:
                edge = prob - (1/odds)
                k = kelly_criterion(prob, odds)
                if edge >= MIN_EDGE and k > 0:
                    recommendations.append({
                        "game": f"{cn(away)} @ {cn(home)}", 
                        "pick": format_pick(t), "odds": odds, "edge": edge, "k": k
                    })

        # 3. 評估讓分盤
        if best_sp[home]["o"] > 0:
            h_point = best_sp[home]["p"]
            p_h_sp = estimate_spread_prob(avg_p_home, h_point)
            
            # 主隊讓/受讓
            odds_h = best_sp[home]["o"]
            if MIN_ODDS <= odds_h <= MAX_ODDS:
                edge_h = p_h_sp - (1/odds_h)
                k_h = kelly_criterion(p_h_sp, odds_h)
                if edge_h >= MIN_EDGE and k_h > 0:
                    recommendations.append({
                        "game": f"{cn(away)} @ {cn(home)}", 
                        "pick": format_pick(home, h_point), "odds": odds_h, "edge": edge_h, "k": k_h
                    })

            # 客隊讓/受讓
            odds_a = best_sp[away]["o"]
            if MIN_ODDS <= odds_a <= MAX_ODDS:
                p_a_sp = 1 - p_h_sp
                edge_a = p_a_sp - (1/odds_a)
                k_a = kelly_criterion(p_a_sp, odds_a)
                if edge_a >= MIN_EDGE and k_a > 0:
                    recommendations.append({
                        "game": f"{cn(away)} @ {cn(home)}", 
                        "pick": format_pick(away, best_sp[away]["p"]), "odds": odds_a, "edge": edge_a, "k": k_a
                    })

    # 4. 輸出與發送
    if not recommendations:
        send_discord("⚖️ 今日市場暫無具備足夠優勢的標的。穩定為上，建議觀望。")
        return

    # 按優勢排序，但不限制輸出數量
    recommendations.sort(key=lambda x: x["edge"], reverse=True)
    
    msg = f"📊 **NBA 穩定型價值分析報告 (Edge > {MIN_EDGE:.1%})**\n"
    msg += f"📅 執行時間：{datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += "---"
    for r in recommendations:
        msg += f"\n🏀 **{r['game']}**"
        msg += f"\n推薦：`{r['pick']}`"
        msg += f"\n賠率：`{r['odds']}` | 優勢：`{r['edge']:.1%}`"
        msg += f"\n建議水位：`{r['k']:.1%}` 資金\n"
    
    send_discord(msg)

def send_discord(text):
    requests.post(WEBHOOK_URL, json={"content": text})

if __name__ == "__main__":
    analyze()
