import requests
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ===== V16.0 Dual-Mode 參數 =====
STRICT_EDGE = 0.022         # 第一階段：獵人模式門檻 (2.2%)
BUY_POINT_EDGE = 0.018      # 第二階段：買分模式門檻 (1.8%)
KELLY_CAP = 0.05            # 單場最高倉位 5%
SPREAD_COEF = 0.20          # 讓分敏感度校正
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

def get_penalty(point):
    abs_pt = abs(point)
    if abs_pt > 15: return 0.045  # 深盤嚴厲懲罰
    if abs_pt >= 8.5: return 0.015 # 中盤輕微懲罰
    return 0

def kelly(prob, odds):
    b = odds - 1
    if prob <= 1/odds: return 0
    return min(round(max(0, (prob * b - (1 - prob)) / b), 4), KELLY_CAP)

def run_analysis(games, mode="Strict"):
    found_picks = []
    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        
        home_en, away_en = g["home_team"], g["away_team"]
        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        m_list = bookmakers[0].get("markets", [])
        h2h = next((m["outcomes"] for m in m_list if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in m_list if m["key"] == "spreads"), None)
        if not h2h: continue

        # 基礎概率推算
        h_ml = next(o for o in h2h if o["name"] == home_en)["price"]
        a_ml = next(o for o in h2h if o["name"] == away_en)["price"]
        p_home = min((1/h_ml) / ((1/h_ml) + (1/a_ml)) + 0.02, 0.95)
        p_away = 1 - p_home

        candidates = []
        if mode == "Strict":
            # 原始邏輯：獵人模式
            if spreads:
                for o in spreads:
                    pt, odds = o["point"], o["price"]
                    p_spread = 0.5 + ((p_home if o["name"] == home_en else p_away) - 0.5) * SPREAD_COEF
                    edge = p_spread - (1/odds) - get_penalty(pt)
                    if edge >= STRICT_EDGE and ODDS_MIN <= odds <= ODDS_MAX:
                        candidates.append({"pick": f"{'讓分' if pt<0 else '受讓'}({pt:+})：{cn(o['name'])}", "odds": odds, "edge": edge, "prob": p_spread})
        else:
            # 進階邏輯：買分模式 (自動少讓 1 分)
            if spreads:
                for o in spreads:
                    pt, odds = o["point"], o["price"]
                    adj_pt = pt + 1 if pt < 0 else pt - 1 # 買 1 分
                    adj_odds = odds - 0.15               # 買分賠率衰減模擬
                    p_spread = 0.5 + ((p_home if o["name"] == home_en else p_away) - 0.5) * SPREAD_COEF
                    edge = p_spread - (1/adj_odds) - get_penalty(adj_pt)
                    if edge >= BUY_POINT_EDGE and ODDS_MIN <= adj_odds <= ODDS_MAX:
                        candidates.append({"pick": f"🛡️ 買分({adj_pt:+})：{cn(o['name'])}", "odds": adj_odds, "edge": edge, "prob": p_spread})

        if candidates:
            candidates.sort(key=lambda x: x["edge"], reverse=True)
            best = candidates[0]
            found_picks.append({
                "game": f"{cn(away_en)} @ {cn(home_en)}",
                "date": tw_time.strftime('%m/%d (週%w)'),
                "pick": best["pick"], "odds": best["odds"], "edge": best["edge"], "prob": best["prob"]
            })
    return found_picks

def main():
    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"})
        games = res.json()
    except Exception as e:
        print(f"Error: {e}"); return

    # Step 1: 優先跑獵人模式
    picks = run_analysis(games, mode="Strict")
    current_mode = "🎯 獵人模式 (高優勢)"

    # Step 2: 若沒推薦，自動切換至買分模式
    if not picks:
        picks = run_analysis(games, mode="BuyPoint")
        current_mode = "🛡️ 買分模式 (高勝率)"

    msg = f"🛰️ NBA V16.0 Dual-Mode - {datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += f"*(策略運作：{current_mode})*\n"

    if not picks:
        msg += "\n🚫 今日所有場次（含買分後）皆無投資價值，建議空倉觀察。"
    else:
        for r in sorted(picks, key=lambda x: x["edge"], reverse=True):
            msg += f"\n📅 {r['date']} | **{r['game']}**\n"
            msg += f"> 💰 {r['pick']} | 賠率：{r['odds']:.2f}\n"
            msg += f"> 📈 優勢：{r['edge']:.2%} | 倉位：{kelly(r['prob'], r['odds']):.2%}\n"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
