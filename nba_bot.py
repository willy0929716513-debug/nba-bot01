import requests
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ===== NBA V22.0 The Sniper (狙擊手量化版) =====
STRICT_EDGE_BASE = 0.020    
TOTAL_EDGE_BASE = 0.022     
KELLY_CAP = 0.025

SPREAD_COEF = 0.16
DEEP_SPREAD_COEF = 0.13
BUY_POINT_FACTOR = 0.90

API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

TEAM_CN = {
    "Los Angeles Lakers": "湖人","Golden State Warriors": "勇士","Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿","Denver Nuggets": "金塊","Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽","LA Clippers": "快艇","Miami Heat": "熱火",
    "Philadelphia 76ers": "七六人","Sacramento Kings": "國王","New Orleans Pelicans": "尼克",
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
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"})
        games = res.json()
    except: return

    game_analysis = defaultdict(dict)

    for g in games:
        home_en, away_en = g["home_team"], g["away_team"]
        game_key = f"{cn(away_en)} @ {cn(home_en)}"
        markets = g.get("bookmakers", [{}])[0].get("markets", [])
        
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)
        totals = next((m["outcomes"] for m in markets if m["key"] == "totals"), None)
        if not h2h: continue

        # --- 基礎數據 ---
        h_ml = next(o["price"] for o in h2h if o["name"] == home_en)
        a_ml = next(o["price"] for o in h2h if o["name"] == away_en)
        p_home = (1/h_ml) / ((1/h_ml) + (1/a_ml))
        strength_gap = abs(p_home - 0.5)

        # --- 讓分分析 (Bias & Edge Scaling) ---
        best_spread = {"edge": -1, "type": "spread"}
        if spreads:
            for o in spreads:
                pt, odds = o["point"], o["price"]
                is_home = o["name"] == home_en
                base_p = p_home if is_home else (1-p_home)
                
                bias = (base_p - 0.5)
                coef = DEEP_SPREAD_COEF if abs(pt) > 12 else SPREAD_COEF
                p_spread = 0.5 + (bias * coef) + (bias * 0.06)
                
                penalty_ratio = abs(pt) * 0.0025
                if not is_home and abs(pt) > 8:
                    penalty_ratio += 0.003

                if 7 <= abs(pt) <= 11:
                    f_p = p_spread + (abs(pt) * 0.0015)
                    f_odds = odds * BUY_POINT_FACTOR
                    f_pt = pt + 1.5 if pt < 0 else pt - 1.5
                    label = "🛡️ 讓分買分"
                else:
                    f_p, f_odds, f_pt, label = p_spread, odds, pt, "🎯 讓分原始"

                f_p = min(max(f_p, 0.05), 0.95)
                raw_diff = f_p - (1/f_odds)
                edge = raw_diff * (1 - penalty_ratio) if raw_diff > 0 else raw_diff

                if edge > best_spread["edge"]:
                    best_spread.update({"pick": f"{label}({f_pt:+})：{cn(o['name'])}", "odds": round(f_odds,2), "edge": edge, "k": kelly(f_p, f_odds), "pt": f_pt})

        # --- 大小分分析 (升級：盤口調節 Line Bias) ---
        best_total = {"edge": -1, "type": "total"}
        if totals:
            for o in totals:
                line, odds = o["point"], o["price"]
                
                # 基礎實力節奏
                if o["name"] == "Over":
                    p_total = 0.5 + (strength_gap * 0.06)
                else:
                    p_total = 0.5 - (strength_gap * 0.04)
                
                # 升級 2：盤口調節 (Line Bias)
                # 盤越高，減去越多 Over 機率；盤越低，加上越多 Over 機率
                line_bias = (line - 228) * 0.0012
                p_total -= line_bias
                
                p_total = min(max(p_total, 0.05), 0.95)
                raw_diff_t = p_total - (1/odds)
                edge = raw_diff_t * (1 - 0.018)
                
                if edge > best_total["edge"]:
                    best_total.update({"pick": f"🏀 {o['name']} {line}", "odds": odds, "edge": edge, "k": kelly(p_total, odds), "pt": line})

        # --- 最終選取 ---
        res_pick = best_total if best_total["edge"] > best_spread["edge"] else best_spread

        # 升級 1：硬性門檻與品質過濾 (紀律執行)
        threshold = TOTAL_EDGE_BASE if res_pick["type"] == "total" else STRICT_EDGE_BASE
        
        if res_pick["edge"] >= threshold and res_pick.get("k", 0) > 0:
            game_analysis[game_key] = res_pick

    # --- 輸出結果 ---
    if not game_analysis:
        return # 如果沒有達標場次，直接結束不發送訊息

    sorted_res = sorted(game_analysis.items(), key=lambda x: x[1]["edge"], reverse=True)
    msg = f"🛰️ **NBA V22.0 The Sniper** - {datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += "*(核心：硬性門檻過濾/盤口重心調節)*\n"

    for g_key, p in sorted_res[:4]:
        msg += f"\n✅ __**{g_key}**__\n👉 **{p['pick']}**\n  └ Edge: `{p['edge']:.2%}` | 賠率: `{p['odds']}` | 倉: `{p['k']:.2%}`\n---"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
