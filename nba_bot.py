import requests
import os
from datetime import datetime, timedelta

# ===== NBA V17.5 Multi-Dimensional 參數 =====
STRICT_EDGE_BASE = 0.018    # 讓分門檻
TOTAL_EDGE_BASE = 0.022     # 大小分門檻 (需更高優勢才開火)
KELLY_CAP = 0.045
SPREAD_COEF = 0.19          # 讓分轉化率
TOTAL_COEF = 0.15           # 大小分轉化率 (較保守)
BUY_POINT_FACTOR = 0.91
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

def main():
    try:
        # 請求 H2H, Spreads, Totals 三個市場
        res = requests.get(BASE_URL, params={
            "apiKey": API_KEY, "regions": "us", 
            "markets": "h2h,spreads,totals", "oddsFormat": "decimal"
        })
        games = res.json()
    except: return

    qualified_picks = []
    potential_picks = []

    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        home_en, away_en = g["home_team"], g["away_team"]
        
        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        markets = bookmakers[0].get("markets", [])
        
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)
        totals = next((m["outcomes"] for m in markets if m["key"] == "totals"), None)
        if not h2h: continue

        # --- 1. 實力對接 (H2H) ---
        h_ml = next(o["price"] for o in h2h if o["name"] == home_en)
        a_ml = next(o["price"] for o in h2h if o["name"] == away_en)
        p_home_real = (1/h_ml) / ((1/h_ml) + (1/a_ml))

        # --- 2. 讓分分析 (Spreads) ---
        if spreads:
            for o in spreads:
                pt, odds = o["point"], o["price"]
                abs_pt = abs(pt)
                base_p = p_home_real if o["name"] == home_en else (1 - p_home_real)
                p_spread = 0.5 + ((base_p - 0.5) * (0.17 if abs_pt > 12 else SPREAD_COEF))
                
                # 判定買分
                if 7 <= abs_pt <= 11 and (p_spread - (1/odds)) >= 0.005:
                    f_pt, f_odds, f_p, label, pen = pt + 1.5 if pt < 0 else pt - 1.5, odds * BUY_POINT_FACTOR, p_spread + 0.045, "🛡️ 讓分買分", 0.005
                else:
                    f_pt, f_odds, f_p, label, pen = pt, odds, p_spread, "🎯 讓分原始", (0.015 if abs_pt > 12 else 0.008)
                
                edge = f_p - (1/f_odds) - pen
                if edge > 0:
                    pick = {"game": f"{cn(away_en)} @ {cn(home_en)}", "pick": f"{label}({f_pt:+})：{cn(o['name'])}", "odds": round(f_odds, 2), "edge": edge, "k": kelly(f_p, f_odds), "type": "SPREAD"}
                    if edge >= STRICT_EDGE_BASE: qualified_picks.append(pick)
                    else: potential_picks.append(pick)

        # --- 3. 大小分分析 (Totals) ---
        if totals:
            for o in totals:
                line, odds = o["point"], o["price"]
                # 模型假設：高強隊對決(H2H接近)通常防守較強，勝負懸殊(H2H遠)通常大分機率微增
                # 這裡引入一個簡單的大分修正：勝率越懸殊，大分機率 +1%
                strength_gap = abs(p_home_real - 0.5)
                p_over = 0.5 + (strength_gap * TOTAL_COEF) if o["name"] == "Over" else 0.5 - (strength_gap * TOTAL_COEF)
                
                # 大小分固定懲罰 1.2%
                edge = p_over - (1/odds) - 0.012
                if edge > 0:
                    pick = {"game": f"{cn(away_en)} @ {cn(home_en)}", "pick": f"🏀 {o['name']} {line}", "odds": odds, "edge": edge, "k": kelly(p_over, odds), "type": "TOTAL"}
                    if edge >= TOTAL_EDGE_BASE: qualified_picks.append(pick)
                    else: potential_picks.append(pick)

    # --- 輸出結果 ---
    msg = f"🛰️ NBA V17.5 Multi-Dim - {datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += "✅ **【推薦單】**\n"
    if not qualified_picks: msg += "> 無達標場次\n"
    else:
        for r in sorted(qualified_picks, key=lambda x: x['edge'], reverse=True):
            msg += f"• {r['game']} | **{r['pick']}** | 賠率:{r['odds']} | Edge:{r['edge']:.2%} | 倉:{r['k']:.2%}\n"
    
    msg += "\n⚠️ **【潛在觀察】**\n"
    if not potential_picks: msg += "> 無正向場次\n"
    else:
        for r in sorted(potential_picks, key=lambda x: x['edge'], reverse=True)[:5]:
            msg += f"• {r['game']} | {r['pick']} | Edge:{r['edge']:.2%}\n"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
