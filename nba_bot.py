import requests
import os
from datetime import datetime, timedelta

# ===== 環境變數設定 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# ===== 穩定獲利核心參數 =====
PRIMARY_EDGE = 0.015    # 高價值門檻 (1.5% 以上優勢)
SECONDARY_EDGE = 0.005  # 適度參與門檻 (0.5% 以上優勢，確保每日有推薦)
KELLY_FRACTION = 0.05   # 保守型凱利比例 (5%)，控制波動
MIN_ODDS = 1.35         # 避開過熱場次
MAX_ODDS = 3.5          # 避開極端冷門

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# ===== 中文映射表 =====
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

# ===== 核心計算邏輯 =====

def get_no_vig_prob(h2h_outcomes):
    """計算市場去抽水後的公平機率"""
    try:
        inv_sum = sum(1 / o["price"] for o in h2h_outcomes)
        return {o["name"]: (1 / o["price"]) / inv_sum for o in h2h_outcomes}
    except: return None

def kelly_criterion(prob, odds, fraction=KELLY_FRACTION):
    """凱利公式：最優注碼配置"""
    if prob <= (1 / odds): return 0
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return round(k * fraction, 4)

def estimate_spread_prob(win_prob, spread):
    """將不讓分勝率轉換為讓分盤勝率 (1分 = 2.8% 勝率)"""
    adjustment = spread * 0.028 
    return min(max(win_prob + adjustment, 0.05), 0.95)

def format_pick_name(team_name, point=None):
    """自動格式化：顯示主勝或加減號讓分"""
    if point is None:
        return f"{cn(team_name)} (不讓分)"
    return f"{cn(team_name)} {point:+g}"

# ===== 主程式分析 =====

def analyze():
    params = {
        "apiKey": API_KEY, 
        "regions": "us,eu,au", 
        "markets": "h2h,spreads", 
        "oddsFormat": "decimal"
    }
    
    try:
        res = requests.get(BASE_URL, params=params).json()
    except Exception as e:
        print(f"API Error: {e}")
        return

    high_value_picks = []
    secondary_picks = []

    for g in res:
        # 時間過濾 (確保只抓尚未開賽的場次)
        commence_time = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        if commence_time < datetime.now(commence_time.tzinfo): continue

        home, away = g["home_team"], g["away_team"]
        best_h2h = {home: 0, away: 0}
        best_sp = {home: {"p": 0, "o": 0}, away: {"p": 0, "o": 0}}
        all_market_probs = []

        # 1. 數據聚合與 Line Shopping
        for book in g.get("bookmakers", []):
            for m in book.get("markets", []):
                if m["key"] == "h2h":
                    p_dict = get_no_vig_prob(m["outcomes"])
                    if p_dict: all_market_probs.append(p_dict)
                    for o in m["outcomes"]:
                        best_h2h[o["name"]] = max(best_h2h[o["name"]], o["price"])
                
                if m["key"] == "spreads":
                    for o in m["outcomes"]:
                        if o["price"] > best_sp[o["name"]]["o"]:
                            best_sp[o["name"]]["p"] = o["point"]
                            best_sp[o["name"]]["o"] = o["price"]

        if not all_market_probs: continue
        avg_p_home = sum(p[home] for p in all_market_probs) / len(all_market_probs)

        # 2. 在這場比賽中，比對「不讓分」與「讓分盤」哪個 Edge 最高
        temp_game_picks = []

        def evaluate(prob, odds, pick_name):
            if MIN_ODDS <= odds <= MAX_ODDS:
                edge = prob - (1/odds)
                k = kelly_criterion(prob, odds)
                if edge > 0:
                    temp_game_picks.append({
                        "game": f"{cn(away)} @ {cn(home)}", 
                        "pick": pick_name, "odds": odds, "edge": edge, "k": k
                    })

        # --- 評估不讓分盤 ---
        evaluate(avg_p_home, best_h2h[home], format_pick_name(home))
        evaluate(1 - avg_p_home, best_h2h[away], format_pick_name(away))

        # --- 評估讓分盤 ---
        if best_sp[home]["o"] > 0:
            p_h_sp = estimate_spread_prob(avg_p_home, best_sp[home]["p"])
            evaluate(p_h_sp, best_sp[home]["o"], format_pick_name(home, best_sp[home]["p"]))
            evaluate(1 - p_h_sp, best_sp[away]["o"], format_pick_name(away, best_sp[away]["p"]))

        # --- 挑選該場比賽「最穩（優勢最大）」的下法 ---
        if temp_game_picks:
            temp_game_picks.sort(key=lambda x: x["edge"], reverse=True)
            best_choice = temp_game_picks[0] # 該比賽最強標的
            
            if best_choice["edge"] >= PRIMARY_EDGE:
                high_value_picks.append(best_choice)
            elif best_choice["edge"] >= SECONDARY_EDGE:
                secondary_picks.append(best_choice)

    # 3. 輸出邏輯
    final_output = []
    status = ""

    if high_value_picks:
        high_value_picks.sort(key=lambda x: x["edge"], reverse=True)
        final_output = high_value_picks
        status = "🚀 **【高價值標的】系統偵測顯著優勢**"
    elif secondary_picks:
        secondary_picks.sort(key=lambda x: x["edge"], reverse=True)
        final_output = secondary_picks[:3] # 取前 3 強
        status = "⚖️ **【適度關注】市場穩定，僅列出最優選題**"

    if not final_output:
        send_discord("📢 今日市場賠率精確，未偵測到足夠優勢場次，建議觀望。")
        return

    msg = f"{status}\n📅 執行時間：{datetime.now().strftime('%m/%d %H:%M')}\n---"
    for r in final_output:
        msg += f"\n🏀 **{r['game']}**"
        msg += f"\n推薦：`{r['pick']}`"
        msg += f"\n賠率：`{r['odds']}` | 預估優勢：`{r['edge']:.1%}`"
        msg += f"\n建議水位：`{r['k']:.1%}` 總資金\n"
    
    send_discord(msg)

def send_discord(text):
    requests.post(WEBHOOK_URL, json={"content": text})

if __name__ == "__main__":
    analyze()
