import requests
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
 os
from datetime import datetime, timedelta

# ===== 環境變數設定 =====
# 請確保在你的系統環境中設定了這兩個變數
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

if not API_KEY or not WEBHOOK_URL:
    print("❌ 錯誤：請檢查 ODDS_API_KEY 或 DISCORD_WEBHOOK 是否已設定。")

BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

# ===== 中文隊名映射 =====
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
    """計算去抽水後的公平市場機率 (Normalization)"""
    try:
        # 計算賠率倒數總和 (通常 > 1.0)
        inv_sum = sum(1 / o["price"] for o in h2h_outcomes)
        # 歸一化回機率 (總和為 1.0)
        return {o["name"]: (1 / o["price"]) / inv_sum for o in h2h_outcomes}
    except:
        return None

def kelly_criterion(prob, odds, fraction=0.1):
    """
    凱利公式：(bp - q) / b
    fraction: 凱利下注比例，0.1 代表『十分之一凱利』，是極度保守且安全的做法。
    """
    if prob <= (1 / odds): 
        return 0
    b = odds - 1
    k = (prob * b - (1 - prob)) / b
    return round(k * fraction, 4)

def estimate_spread_prob(win_prob, spread):
    """
    根據主隊不讓分勝率與讓分值，估算過盤機率。
    NBA 實證研究：讓分每變動 1 分，勝率約變動 2.5% ~ 3%。
    """
    # spread 為主隊數值，如 -5.5 代表主讓 5.5
    # 若 spread 為負（讓分），過盤難度增加，機率下降
    adjustment = spread * 0.028 
    spread_prob = win_prob + adjustment
    return min(max(spread_prob, 0.05), 0.95)

def format_pick_name(team_name, point=None):
    """格式化輸出推薦字樣，例如：湖人 -5.5 或 勇士 +3"""
    if point is None:
        return f"{cn(team_name)} (不讓分)"
    # 使用 :+g 格式化讓正號出現，且自動處理整數/浮點數
    return f"{cn(team_name)} {point:+g}"

# ===== 主程式邏輯 =====

def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us", # 可根據需求改為 'eu', 'au' 增加博彩商數量
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    try:
        response = requests.get(BASE_URL, params=params)
        games = response.json()
    except Exception as e:
        print(f"API 連結失敗: {e}")
        return

    all_picks = []

    for g in games:
        # --- 時間過濾 ---
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        # 只看台灣時間早上 6 點後的比賽 (避開可能已開賽或深夜場)
        if tw_time.hour < 6: continue

        home = g["home_team"]
        away = g["away_team"]
        
        # --- Line Shopping: 從多個博彩商中抓取最佳賠率 ---
        best_h2h = {home: 0, away: 0}
        best_spread = {"point": 0, home: 0, away: 0}
        market_probs = []

        for book in g.get("bookmakers", []):
            for m in book.get("markets", []):
                # 處理不讓分
                if m["key"] == "h2h":
                    p_dict = get_no_vig_prob(m["outcomes"])
                    if p_dict: market_probs.append(p_dict)
                    for o in m["outcomes"]:
                        best_h2h[o["name"]] = max(best_h2h[o["name"]], o["price"])
                
                # 處理讓分
                if m["key"] == "spreads":
                    # 我們以第一家找到的讓分點數為基準，比較不同家的賠率
                    # 在實務上，點數可能不同（如 -5.5 vs -6.0），此處簡化處理
                    for o in m["outcomes"]:
                        if o["name"] == home:
                            best_spread["point"] = o["point"]
                            best_spread[home] = max(best_spread[home], o["price"])
                        else:
                            best_spread[away] = max(best_spread[away], o["price"])

        if not market_probs: continue

        # 計算市場平均公平機率作為『基準勝率』
        avg_p_home = sum(p[home] for p in market_probs) / len(market_probs)
        
        # --- 策略：尋找 Edge (優勢) ---
        # 這裡的邏輯是：如果市場最佳賠率高於公平隱含機率，則存在獲利空間
        
        # 1. 不讓分檢查
        for team in [home, away]:
            prob = avg_p_home if team == home else (1 - avg_p_home)
            odds = best_h2h[team]
            k = kelly_criterion(prob, odds)
            if k > 0.001: # 排除極小下注
                all_picks.append({
                    "game": f"{cn(away)} @ {cn(home)}",
                    "pick": format_pick_name(team),
                    "odds": odds,
                    "edge": prob - (1 / odds),
                    "kelly": k
                })

        # 2. 讓分盤檢查
        if best_spread[home] > 0 and best_spread[away] > 0:
            h_point = best_spread["point"]
            p_spread_home = estimate_spread_prob(avg_p_home, h_point)
            
            # 主隊過盤評估
            k_h = kelly_criterion(p_spread_home, best_spread[home])
            if k_h > 0.001:
                all_picks.append({
                    "game": f"{cn(away)} @ {cn(home)}",
                    "pick": format_pick_name(home, h_point),
                    "odds": best_spread[home],
                    "edge": p_spread_home - (1 / best_spread[home]),
                    "kelly": k_h
                })

            # 客隊過盤評估 (點數反轉)
            a_point = -h_point
            k_a = kelly_criterion(1 - p_spread_home, best_spread[away])
            if k_a > 0.001:
                all_picks.append({
                    "game": f"{cn(away)} @ {cn(home)}",
                    "pick": format_pick_name(away, a_point),
                    "odds": best_spread[away],
                    "edge": (1 - p_spread_home) - (1 / best_spread[away]),
                    "kelly": k_a
                })

    # --- 整理與發送 ---
    all_picks.sort(key=lambda x: x["edge"], reverse=True)
    top_picks = all_picks[:2]

    if not top_picks:
        send_discord("📢 今日經過篩選，無具備 Edge (優勢) 的 NBA 賽事推薦。")
        return

    msg = "🔥 **NBA 策略分析 V12.1 (精準讓分符號版)**\n"
    msg += f"分析時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    msg += "---"
    for p in top_picks:
        msg += f"\n🏀 **{p['game']}**"
        msg += f"\n推薦：`{p['pick']}`"
        msg += f"\n賠率：`{p['odds']}` | 預估優勢：`{p['edge']:.1%}`"
        msg += f"\n建議倉位：`{p['kelly']:.1%}`\n"

    send_discord(msg)

def send_discord(text):
    try:
        requests.post(WEBHOOK_URL, json={"content": text})
    except Exception as e:
        print(f"Discord 發送失敗: {e}")

if __name__ == "__main__":
    analyze()
