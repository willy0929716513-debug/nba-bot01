import requests
import os
from datetime import datetime, timedelta

# ===== 環境變數 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

if not API_KEY or not WEBHOOK_URL:
    raise ValueError("請確保 API_KEY 與 WEBHOOK_URL 已設定")

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# 中文映射 (保持不變)
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

# ===== 核心邏輯 =====

def get_no_vig_prob(h2h_outcomes):
    """計算去抽水後的公平機率"""
    try:
        inv_sum = sum(1/o["price"] for o in h2h_outcomes)
        # 返回字典 {隊名: 公平機率}
        return {o["name"]: (1/o["price"]) / inv_sum for o in h2h_outcomes}
    except: return None

def kelly_criterion(prob, odds, fraction=0.1):
    """分段凱利公式，預設只投 10% (保守派)"""
    if prob <= (1/odds): return 0
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return round(k * fraction, 4)

def estimate_spread_prob(win_prob, spread):
    """
    根據不讓分勝率與讓分分值，估算讓分盤勝率
    NBA 經驗公式：1 分差距約等於 2.8% 勝率變動
    """
    # spread 是對主隊的讓分，例如 -5.5
    # 如果 win_prob 是 0.5 (平手)，讓 -5.5 分的勝率會大幅下降
    adjustment = spread * 0.028 
    spread_prob = win_prob + adjustment
    return min(max(spread_prob, 0.05), 0.95)

def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us", # 可改為 "eu" 或 "au" 增加更多博彩商
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    try:
        res = requests.get(BASE_URL, params=params)
        games = res.json()
    except Exception as e:
        print(f"API 請求失敗: {e}")
        return

    all_picks = []

    for g in games:
        # 時間過濾 (避開凌晨)
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        if tw_time.hour < 6: continue

        home = g["home_team"]
        away = g["away_team"]
        
        # --- 1. Line Shopping: 找出市場最佳賠率與公平機率 ---
        best_h2h = {home: 0, away: 0}
        best_spread = {"point": 0, home: 0, away: 0}
        market_probs = []

        for book in g["bookmakers"]:
            for m in book["markets"]:
                if m["key"] == "h2h":
                    probs = get_no_vig_prob(m["outcomes"])
                    if probs: market_probs.append(probs)
                    for o in m["outcomes"]:
                        best_h2h[o["name"]] = max(best_h2h[o["name"]], o["price"])
                
                if m["key"] == "spreads":
                    for o in m["outcomes"]:
                        if o["name"] == home:
                            best_spread["point"] = o["point"]
                            best_spread[home] = max(best_spread[home], o["price"])
                        else:
                            best_spread[away] = max(best_spread[away], o["price"])

        if not market_probs: continue

        # 平均市場公平機率作為參考
        avg_p_home = sum(p[home] for p in market_probs) / len(market_probs)
        
        # --- 2. 策略：找出被市場「低估」的最佳賠率 ---
        # 這裡假設如果某家博彩商給出的賠率高於市場平均隱含賠率，則存在 Edge
        # 在實際應用中，你應在此處替換為你自己的 AI 預測勝率
        my_p_home = avg_p_home 

        # 檢查不讓分盤 (Moneyline)
        for team in [home, away]:
            prob = my_p_home if team == home else (1 - my_p_home)
            odds = best_h2h[team]
            k = kelly_criterion(prob, odds)
            if k > 0:
                all_picks.append({
                    "game": f"{cn(away)} @ {cn(home)}",
                    "pick": f"{cn(team)} (不讓分)",
                    "odds": odds,
                    "edge": round(prob - (1/odds), 3),
                    "kelly": k
                })

        # 檢查讓分盤 (Spreads)
        if best_spread[home] > 0:
            sp_prob_home = estimate_spread_prob(my_p_home, best_spread["point"])
            # 檢查主隊讓分
            k_h = kelly_criterion(sp_prob_home, best_spread[home])
            if k_h > 0:
                all_picks.append({
                    "game": f"{cn(away)} @ {cn(home)}",
                    "pick": f"{cn(home)} (讓分 {best_spread['point']})",
                    "odds": best_spread[home],
                    "edge": round(sp_prob_home - (1/best_spread[home]), 3),
                    "kelly": k_h
                })
            # 檢查客隊讓分 (反向機率)
            k_a = kelly_criterion(1-sp_prob_home, best_spread[away])
            if k_a > 0:
                all_picks.append({
                    "game": f"{cn(away)} @ {cn(home)}",
                    "pick": f"{cn(away)} (讓分 {-best_spread['point']})",
                    "odds": best_spread[away],
                    "edge": round((1-sp_prob_home) - (1/best_spread[away]), 3),
                    "kelly": k_a
                })

    # --- 3. 輸出最優兩場 ---
    all_picks.sort(key=lambda x: x["edge"], reverse=True)
    top_picks = all_picks[:2]

    if not top_picks:
        send_discord("今日無足夠優勢賽事。")
    else:
        msg = "🚀 **NBA 策略分析 V12.0 (Line Shopping + 10% Kelly)**\n"
        for p in top_picks:
            msg += f"\n🏀 **{p['game']}**\n推薦：{p['pick']}\n賠率：{p['odds']} | Edge：{p['edge']:.1%}\n建議水位：{p['kelly']:.1%} 倉位\n"
        send_discord(msg)

def send_discord(text):
    requests.post(WEBHOOK_URL, json={"content": text})

if __name__ == "__main__":
    analyze()
