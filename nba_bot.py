import requests
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ===== NBA V18.9 Equilibrium Pivot (平衡修正) =====
STRICT_EDGE_BASE = 0.015    # 平衡門檻：讓分 1.5%
TOTAL_EDGE_BASE = 0.020     # 平衡門檻：大小分 2.0%
KELLY_CAP = 0.025           # 低 Edge 環境下限制倉位為 2.5%

SPREAD_COEF = 0.16
DEEP_SPREAD_COEF = 0.14
TOTAL_COEF = 0.12
BUY_POINT_FACTOR = 0.90
ODDS_MIN, ODDS_MAX = 1.40, 3.20

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
    raw = (prob*b - (1-prob)) / b
    return min(max(0, raw), KELLY_CAP)

def main():
    try:
        # 使用 2026 年當前時間進行 API 請求
        res = requests.get(BASE_URL, params={
            "apiKey": API_KEY, "regions": "us", 
            "markets": "h2h,spreads,totals", "oddsFormat": "decimal"
        })
        games = res.json()
    except: return

    dated_picks = defaultdict(list)

    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        date_str = tw_time.strftime('%m/%d')
        home_en, away_en = g["home_team"], g["away_team"]
        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        markets = bookmakers[0].get("markets", [])

        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)
        totals = next((m["outcomes"] for m in markets if m["key"] == "totals"), None)
        if not h2h: continue

        h_ml = next(o["price"] for o in h2h if o["name"] == home_en)
        a_ml = next(o["price"] for o in h2h if o["name"] == away_en)
        p_home = (1/h_ml) / ((1/h_ml) + (1/a_ml))

        # ---- SPREAD (讓分) ----
        if spreads:
            for o in spreads:
                pt, odds = o["point"], o["price"]
                if not (ODDS_MIN <= odds <= ODDS_MAX): continue
                abs_pt = abs(pt)
                base_p = p_home if o["name"] == home_en else (1 - p_home)
                p_spread = 0.5 + ((base_p - 0.5) * (DEEP_SPREAD_COEF if abs_pt > 12 else SPREAD_COEF))
                
                penalty = 0.012 if abs_pt > 10 else 0.008
                if 7 <= abs_pt <= 11:
                    f_p, f_odds, label = p_spread + (0.015 if abs_pt < 9 else 0.02), odds * BUY_POINT_FACTOR, "🛡️ 買分"
                else:
                    f_p, f_odds, label = p_spread, odds, "🎯 原始"

                edge = f_p - (1/f_odds) - penalty
                if edge >= STRICT_EDGE_BASE:
                    dated_picks[date_str].append({"game": f"{cn(away_en)} @ {cn(home_en)}", "pick": f"{label}({pt:+})：{cn(o['name'])}", "odds": round(f_odds,2), "edge": edge, "k": kelly(f_p, f_odds)})

        # ---- TOTAL (大小分) ----
        if totals:
            for o in totals:
                line, odds = o["point"], o["price"]
                gap = abs(p_home - 0.5)
                p_total = 0.5 + (gap * TOTAL_COEF) if o["name"] == "Over" else 0.5 - (gap * TOTAL_COEF)
                penalty = 0.015 + (0.015 if line > 230 else 0) + (0.01 if line > 235 else 0)
                
                edge = p_total - (1/odds) - penalty
                if edge >= TOTAL_EDGE_BASE:
                    dated_picks[date_str].append({"game": f"{cn(away_en)} @ {cn(home_en)}", "pick": f"🏀 {o['name']} {line}", "odds": odds, "edge": edge, "k": kelly(p_total, odds)})

    # ---- 輸出訊息 ----
    msg = f"🛰️ NBA V18.9 Equilibrium - {datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += "*(當前設定：平衡模式 - 讓分 1.5% / 大小 2.0%)*\n"
    
    if not dated_picks:
        msg += "\n> 市場效率極高，即使在平衡模式下仍無明顯漏洞。"
    else:
        for date in sorted(dated_picks.keys()):
            msg += f"\n📅 **日期：{date}**\n"
            for r in sorted(dated_picks[date], key=lambda x: x["edge"], reverse=True):
                msg += f"• {r['game']} | **{r['pick']}** | 賠率:{r['odds']} | Edge:{r['edge']:.2%} | 倉:{r['k']:.2%}\n"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
