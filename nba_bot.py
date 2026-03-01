import requests
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ===== NBA V18.8 Panoramic Vision 參數 =====
STRICT_EDGE_BASE = 0.025
TOTAL_EDGE_BASE = 0.030
KELLY_CAP = 0.040
# 移除 MAX_PLAYS，顯示所有具價值的場次

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

def get_rating(edge):
    if edge >= 0.035: return "⭐⭐⭐ [機構級別]"
    if edge >= 0.028: return "⭐⭐ [高價值]"
    return "⭐ [標準建議]"

def main():
    try:
        res = requests.get(BASE_URL, params={
            "apiKey": API_KEY, "regions": "us", 
            "markets": "h2h,spreads,totals", "oddsFormat": "decimal"
        })
        games = res.json()
    except: return

    # 使用字典按日期分類：{ "03/01": [picks...], "03/02": [picks...] }
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

        # ---- 讓分分析 (SPREAD) ----
        if spreads:
            for o in spreads:
                pt, odds = o["point"], o["price"]
                if not (ODDS_MIN <= odds <= ODDS_MAX): continue
                abs_pt = abs(pt)
                base_p = p_home if o["name"] == home_en else (1 - p_home)
                coef = DEEP_SPREAD_COEF if abs_pt > 12 else SPREAD_COEF
                p_spread = 0.5 + ((base_p - 0.5) * coef)
                penalty = 0.012 if abs_pt > 10 else 0.008

                if 7 <= abs_pt <= 11:
                    f_p = p_spread + (0.015 if abs_pt < 9 else 0.02)
                    f_odds = odds * BUY_POINT_FACTOR
                    label = "🛡️ 買分"
                else:
                    f_p, f_odds, label = p_spread, odds, "🎯 原始"

                edge = f_p - (1/f_odds) - penalty
                if edge >= STRICT_EDGE_BASE:
                    dated_picks[date_str].append({
                        "game": f"{cn(away_en)} @ {cn(home_en)}",
                        "pick": f"{label}({pt:+})：{cn(o['name'])}",
                        "odds": round(f_odds,2), "edge": edge, "k": kelly(f_p, f_odds)
                    })

        # ---- 大小分分析 (TOTAL) ----
        if totals:
            for o in totals:
                line, odds = o["point"], o["price"]
                if not (ODDS_MIN <= odds <= ODDS_MAX): continue
                gap = abs(p_home - 0.5)
                p_total = 0.5 + (gap * TOTAL_COEF) if o["name"] == "Over" else 0.5 - (gap * TOTAL_COEF)
                
                penalty = 0.015
                if line > 230: penalty += 0.015
                if line > 235: penalty += 0.01
                
                edge = p_total - (1/odds) - penalty
                if edge >= TOTAL_EDGE_BASE:
                    dated_picks[date_str].append({
                        "game": f"{cn(away_en)} @ {cn(home_en)}",
                        "pick": f"🏀 {o['name']} {line}",
                        "odds": odds, "edge": edge, "k": kelly(p_total, odds)
                    })

    # ---- 整理並輸出 ----
    msg = f"🛰️ NBA V18.8 Panoramic Vision - {datetime.now().strftime('%m/%d %H:%M')}\n"
    
    if not dated_picks:
        msg += "\n> 市場定價極度精確，目前無價值場次。"
    else:
        # 按日期排序
        for date in sorted(dated_picks.keys()):
            msg += f"\n📅 **日期：{date}**\n"
            msg += "---"
            # 日期內按 Edge 推薦度排序
            day_picks = sorted(dated_picks[date], key=lambda x: x["edge"], reverse=True)
            for r in day_picks:
                rating = get_rating(r['edge'])
                msg += f"\n• {r['game']} | **{r['pick']}**\n"
                msg += f"  └ {rating} | 賠率:{r['odds']} | Edge:{r['edge']:.2%} | 倉:{r['k']:.2%}\n"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
