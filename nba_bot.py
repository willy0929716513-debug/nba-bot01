import requests
import os
from datetime import datetime, timedelta

# ===== NBA V17.2 Equilibrium 參數平衡設定 =====
STRICT_EDGE_BASE = 0.018    # 平衡門檻：從 2.2% 調降至 1.8% (增加開火機會)
BRIDGE_EDGE_MIN = 0.014     # 買分門檻：同步下調
KELLY_CAP = 0.045           # 凱利倉位上限 4.5% (穩健配置)
SPREAD_COEF = 0.19          # 讓分勝率轉化系數
BUY_POINT_FACTOR = 0.91     # 買 1.5 分的賠率衰減 (Odds * 0.91)
ODDS_MIN, ODDS_MAX = 1.35, 3.50

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

TEAM_CN = {
    "Los Angeles Lakers": "湖人","Golden State Warriors": "勇士","Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿","Denver Nuggets": "金塊","Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽","LA Clippers": "快艇","Miami Heat": "熱火",
    "Philadelphia 76ers": "七六人","Sacramento Kings": "國王","New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼","Dallas Mavericks": "獨行俠","New York Knicks": "尼克",
    "Orlando Magic": "魔術","Charlotte Hornets": "黃蜂","Detroit Pistons": "活塞",
    "Toronto Raptors": "暴龍","Chicago Bulls": "公牛","San Antonio Spurs": "馬刺",
    "Utah Jazz": "爵士","Brooklyn Nets": "籃網","Atlanta Hawks": "老鷹",
    "Cleveland Cavaliers": "騎士","Indiana Pacers": "溜馬","Memphis Grizzlies": "灰熊",
    "Portland Trail Blazers": "拓荒者","Washington Wizards": "巫師","Houston Rockets": "火箭"
}

def cn(t): return TEAM_CN.get(t, t)

def kelly(prob, odds):
    if odds <= 1: return 0
    b = odds - 1
    raw = (prob * b - (1 - prob)) / b
    return min(max(0, raw), KELLY_CAP)

def get_penalty(point):
    # V17.2 平衡懲罰：深盤 1.5%，一般盤 0.8%
    return 0.015 if abs(point) > 12 else 0.008

def main():
    try:
        # 抓取獨贏(h2h)與讓分(spreads)
        res = requests.get(BASE_URL, params={
            "apiKey": API_KEY,
            "regions": "us",
            "markets": "h2h,spreads",
            "oddsFormat": "decimal"
        })
        games = res.json()
    except Exception as e:
        print(f"API Error: {e}")
        return

    picks = []

    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        home_en, away_en = g["home_team"], g["away_team"]
        
        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        markets = bookmakers[0].get("markets", [])
        
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)
        if not h2h or not spreads: continue

        # --- 1. 實力對接：從獨贏賠率換算真實勝率 (移除抽水) ---
        try:
            h_ml = next(o["price"] for o in h2h if o["name"] == home_en)
            a_ml = next(o["price"] for o in h2h if o["name"] == away_en)
            p_home_real = (1/h_ml) / ((1/h_ml) + (1/a_ml))
        except: continue

        for o in spreads:
            pt, odds = o["point"], o["price"]
            abs_pt = abs(pt)
            if not (ODDS_MIN <= odds <= ODDS_MAX): continue

            # --- 2. 計算基礎讓分勝率 ---
            base_p = p_home_real if o["name"] == home_en else (1 - p_home_real)
            # 深盤系數稍微收斂，增加防禦力
            coef = 0.17 if abs_pt > 12 else SPREAD_COEF
            p_spread = 0.5 + ((base_p - 0.5) * coef)
            
            # 先算原始 Edge 作為買分判斷依據
            original_edge = p_spread - (1/odds)

            # --- 3. V17.2 平衡決策邏輯 (Safe Bridge) ---
            if 7 <= abs_pt <= 11 and original_edge >= 0.005:
                # 符合「卡分避險區」，執行買分
                final_pt = pt + 1.5 if pt < 0 else pt - 1.5
                final_odds = odds * BUY_POINT_FACTOR
                final_p = p_spread + 0.045  # 買 1.5 分獲得約 4.5% 勝率補償
                penalty = 0.005             # 買分後風險降低，使用極低懲罰
                threshold = BRIDGE_EDGE_MIN
                label = "🛡️ 避險買分"
            else:
                # 深盤或小盤，維持原始攻擊力
                final_pt, final_odds = pt, odds
                final_p = p_spread
                penalty = get_penalty(pt)
                threshold = STRICT_EDGE_BASE
                label = "🎯 原始盤口"

            # --- 4. 最終 Edge 與 倉位計算 ---
            edge = final_p - (1/final_odds) - penalty
            k = kelly(final_p, final_odds)

            # 達標篩選
            if edge >= threshold and k > 0:
                picks.append({
                    "game": f"{cn(away_en)} @ {cn(home_en)}",
                    "date": tw_time.strftime('%m/%d'),
                    "pick": f"{label}({final_pt:+})：{cn(o['name'])}",
                    "odds": round(final_odds, 2),
                    "edge": edge,
                    "kelly": k
                })

    # --- 5. 發送結果 ---
    msg = f"🛰️ NBA V17.2 Equilibrium - {datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += f"*(策略：平衡優化 - 門檻 1.8% / 深盤懲罰 1.5%)*\n"

    if not picks:
        msg += "\n🚫 今日市場價格精確，無符合平衡優化條件之場次。"
    else:
        # 按 Edge 排序輸出
        for r in sorted(picks, key=lambda x: x["edge"], reverse=True):
            msg += f"\n📅 {r['date']} | **{r['game']}**\n"
            msg += f"> 💰 {r['pick']} | 賠率：{r['odds']:.2f}\n"
            msg += f"> 📈 Edge：{r['edge']:.2%} | 倉位：{r['kelly']:.2%}\n"

    # 執行 Webhook 傳送
    if WEBHOOK_URL:
        requests.post(WEBHOOK_URL, json={"content": msg})
    else:
        print(msg)

if __name__ == "__main__":
    main()
