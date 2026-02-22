import requests
import os
from datetime import datetime, timedelta

# ===== 環境變數設定 =====
# 請確保在你的系統環境中設定了這兩個變數，或直接在此替換字串
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# ===== 策略參數設定 (穩定獲利核心) =====
PRIMARY_EDGE = 0.015    # 高價值門檻 (1.5% 優勢)
SECONDARY_EDGE = 0.005  # 適度參與門檻 (0.5% 優勢，確保每天有場次)
KELLY_FRACTION = 0.05   # 保守型凱利比例 (5%)，分散放寬門檻後的風險
MIN_ODDS = 1.35         # 避開過熱場次 (風險收益不成比例)
MAX_ODDS = 3.0          # 避開極端冷門 (波動過大，不利穩定獲利)

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# ===== 中文隊名映射表 =====
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

def cn(team): 
    return TEAM_CN.get(team, team)

# ===== 核心數學函式 =====

def get_no_vig_prob(h2h_outcomes):
    """計算市場去抽水後的公平機率 (Market Consensus)"""
    try:
        inv_sum = sum(1 / o["price"] for o in h2h_outcomes)
        return {o["name"]: (1 / o["price"]) / inv_sum for o in h2h_outcomes}
    except:
        return None

def kelly_criterion(prob, odds, fraction=KELLY_FRACTION):
    """凱利公式：計算最優注碼佔比"""
    if prob <= (1 / odds): 
        return 0
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return round(k * fraction, 4)

def estimate_spread_prob(win_prob, spread):
    """
    NBA 讓分機率轉換模型。
    原理：NBA 比賽結果分佈接近常態分佈，1分分差約等於 2.8% 的勝率變動。
    """
    # spread 為主隊數值，如 -5.5 代表主讓 5.5
    adjustment = spread * 0.028 
    spread_prob = win_prob + adjustment
    return min(max(spread_prob, 0.05), 0.95)

def format_pick(team_name, point=None):
    """格式化輸出，例如：湖人 -5.5 或 勇士 (不讓分)"""
    if point is None:
        return f"{cn(team_name)} (不讓分)"
    return f"{cn(team_name)} {point:+g}"

# ===== 主分析邏輯 =====

def analyze():
    # 擴大區域至 us, eu, au，增加 Line Shopping 的發現機率
    params = {
        "apiKey": API_KEY, 
        "regions": "us,eu,au", 
        "markets": "h2h,spreads", 
        "oddsFormat": "decimal"
    }
    
    try:
        response = requests.get(BASE_URL, params=params)
        res = response.json()
    except Exception as e:
        print(f"API 請求錯誤: {e}")
        return

    high_value_picks = []
    secondary_picks = []

    for g in res:
        # 時間過濾 (僅分析尚未開賽的場次)
        commence_time = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        if commence_time < datetime.now(commence_time.tzinfo): 
            continue

        home, away = g["home_team"], g["away_team"]
        
        # 聚合數據
        best_h2h = {home: 0, away: 0}
        best_sp = {home: {"p": 0, "o": 0}, away: {"p": 0, "o": 0}}
        all_market_probs = []

        for book in g.get("bookmakers", []):
            for m in book.get("markets", []):
                if m["key"] == "h2h":
                    p_dict = get_no_vig_prob(m["outcomes"])
                    if p_dict: 
                        all_market_probs.append(p_dict)
                    for o in m["outcomes"]:
                        best_h2h[o["name"]] = max(best_h2h[o["name"]], o["price"])
                
                if m["key"] == "spreads":
                    for o in m["outcomes"]:
                        if o["price"] > best_sp[o["name"]]["o"]:
                            best_sp[o["name"]]["p"] = o["point"]
                            best_sp[o["name"]]["o"] = o["price"]

        if not all_market_probs: 
            continue

        # 以市場平均去抽水機率作為基準勝率
        avg_p_home = sum(p[home] for p in all_market_probs) / len(all_market_probs)

        # 內部評估函式
        def evaluate(prob, odds, game_name, pick_name):
            if MIN_ODDS <= odds <= MAX_ODDS:
                edge = prob - (1 / odds)
                k = kelly_criterion(prob, odds)
                data = {"game": game_name, "pick": pick_name, "odds": odds, "edge": edge, "k": k}
                
                if edge >= PRIMARY_EDGE:
                    high_value_picks.append(data)
                elif edge >= SECONDARY_EDGE:
                    secondary_picks.append(data)

        # 1. 檢查不讓分
        for t in [home, away]:
            p = avg_p_home if t == home else (1 - avg_p_home)
            evaluate(p, best_h2h[t], f"{cn(away)} @ {cn(home)}", format_pick(t))

        # 2. 檢查讓分盤
        if best_sp[home]["o"] > 0:
            p_h_sp = estimate_spread_prob(avg_p_home, best_sp[home]["p"])
            # 主隊側
            evaluate(p_h_sp, best_sp[home]["o"], f"{cn(away)} @ {cn(home)}", format_pick(home, best_sp[home]["p"]))
            # 客隊側
            evaluate(1 - p_h_sp, best_sp[away]["o"], f"{cn(away)} @ {cn(home)}", format_pick(away, best_sp[away]["p"]))

    # --- 輸出生成 ---
    output_picks = []
    header = ""

    if high_value_picks:
        high_value_picks.sort(key=lambda x: x["edge"], reverse=True)
        output_picks = high_value_picks
        header = "🚀 **【高價值推薦】系統偵測到顯著優勢**"
    elif secondary_picks:
        secondary_picks.sort(key=lambda x: x["edge"], reverse=True)
        output_picks = secondary_picks[:3] # 若無高價值，取前三場相對優勢標的
        header = "⚖️ **【適度關注】市場穩定，僅列出相對優質標的**"

    if not output_picks:
        send_discord("📢 今日 NBA 市場賠率極其精確，無具備優勢之標的。建議觀望，保護資金。")
        return

    msg = f"{header}\n📅 執行時間：{datetime.now().strftime('%m/%d %H:%M')}\n---"
    for r in output_picks:
        msg += f"\n🏀 **{r['game']}**"
        msg += f"\n推薦：`{r['pick']}`"
        msg += f"\n最佳賠率：`{r['odds']}` | 預估優勢：`{r['edge']:.1%}`"
        msg += f"\n建議水位：`{r['k']:.1%}` 總資金\n"
    
    send_discord(msg)

def send_discord(text):
    try:
        requests.post(WEBHOOK_URL, json={"content": text})
    except Exception as e:
        print(f"Discord 發送失敗: {e}")

if __name__ == "__main__":
    analyze()
