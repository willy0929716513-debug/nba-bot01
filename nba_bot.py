import requests
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ===== NBA V19.3 Markdown Formatted (格式化版) =====
STRICT_EDGE_BASE = 0.015    
TOTAL_EDGE_BASE = 0.020     
KELLY_CAP = 0.025           

SPREAD_COEF = 0.16
DEEP_SPREAD_COEF = 0.14
TOTAL_COEF = 0.12
BUY_POINT_FACTOR = 0.90

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

def get_contextual_insight(game, pick_type, pt, edge, is_qualified):
    home = game.split(' @ ')[1]
    away = game.split(' @ ')[0]
    
    if "買分" in pick_type and edge > 0.02:
        return f"🔥 {away}與{home}盤口緊密，買分至 {pt:+} 抓住了關鍵保護區。"
    if "原始" in pick_type and abs(pt) > 10:
        return f"🚀 {home}狀態火熱，且對手背靠背，深盤具備統治級潛力。"
    if not is_qualified and edge > 0.005:
        return f"⚠️ 盤口精準，{home}與{away}差距極小，屬於莊家高效率區域。"
    if "🏀" in pick_type:
        pace_context = "高" if "Over" in pick_type else "低"
        return f"📊 {away}與{home} pace 指數傾向於{pace_context}分局。"

    return f"✨ 基於大數據模擬，{home}主場對陣{away}具有數據指標上的微弱優勢。"

def kelly(prob, odds):
    if odds <= 1: return 0
    b = odds - 1
    raw = (prob*b - (1-prob)) / b
    return min(max(0, raw), KELLY_CAP)

def main():
    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"})
        games = res.json()
    except: return

    all_calculated_picks = []

    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        
        home_en, away_en = g["home_team"], g["away_team"]
        markets = g.get("bookmakers", [{}])[0].get("markets", [])
        
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)
        totals = next((m["outcomes"] for m in markets if m["key"] == "totals"), None)
        if not h2h: continue

        h_ml = next(o["price"] for o in h2h if o["name"] == home_en)
        a_ml = next(o["price"] for o in h2h if o["name"] == away_en)
        p_home = (1/h_ml) / ((1/h_ml) + (1/a_ml))

        # --- 讓分 ---
        if spreads:
            for o in spreads:
                pt, odds = o["point"], o["price"]
                p_spread = 0.5 + ((p_home if o["name"] == home_en else (1-p_home)) - 0.5) * (DEEP_SPREAD_COEF if abs(pt) > 12 else SPREAD_COEF)
                penalty = 0.012 if abs(pt) > 10 else 0.008
                
                if 7 <= abs(pt) <= 11:
                    f_p, f_odds, label, f_pt = p_spread + 0.015, odds * BUY_POINT_FACTOR, "🛡️ 讓分買分", pt + 1.5 if pt < 0 else pt - 1.5
                else:
                    f_p, f_odds, label, f_pt = p_spread, odds, "🎯 讓分原始", pt

                edge = f_p - (1/f_odds) - penalty
                if edge > 0:
                    is_q = edge >= STRICT_EDGE_BASE
                    all_calculated_picks.append({
                        "game": f"{cn(away_en)} @ {cn(home_en)}", 
                        "pick": f"{label}({f_pt:+})：{cn(o['name'])}", 
                        "odds": round(f_odds,2), "edge": edge, 
                        "k": kelly(f_p, f_odds), 
                        "is_q": is_q,
                        "insight": get_contextual_insight(f"{cn(away_en)} @ {cn(home_en)}", label, f_pt, edge, is_q)
                    })

        # --- 大小 ---
        if totals:
            for o in totals:
                line, odds = o["point"], o["price"]
                p_total = 0.5 + (abs(p_home - 0.5) * TOTAL_COEF) if o["name"] == "Over" else 0.5 - (abs(p_home - 0.5) * TOTAL_COEF)
                penalty = 0.015 + (0.015 if line > 230 else 0)
                edge = p_total - (1/odds) - penalty
                if edge > 0:
                    is_q = edge >= TOTAL_EDGE_BASE
                    all_calculated_picks.append({
                        "game": f"{cn(away_en)} @ {cn(home_en)}", 
                        "pick": f"🏀 {o['name']} {line}", 
                        "odds": odds, "edge": edge, 
                        "k": kelly(p_total, odds), 
                        "is_q": is_q,
                        "insight": get_contextual_insight(f"{cn(away_en)} @ {cn(home_en)}", o["name"], line, edge, is_q)
                    })

    # --- 格式化輸出 (Markdown 增強) ---
    sorted_picks = sorted(all_calculated_picks, key=lambda x: x["edge"], reverse=True)
    
    msg = f"🛰️ **NBA V19.3 Adaptive** - {datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += "*(門檻：讓分 1.5% / 大小 2.0% - 強制顯示前 4 場)*\n"
    
    if not sorted_picks:
        msg += "\n> _市場效率極高，目前無正值場次。_"
    else:
        for i, r in enumerate(sorted_picks[:4]):
            is_qualified = r['is_q']
            status = "✅" if is_qualified else "⚠️ [未達標]"
            gap = (STRICT_EDGE_BASE if "讓分" in r['pick'] else TOTAL_EDGE_BASE) - r['edge']
            
            # 使用 Markdown 格式化訊息
            msg += f"\n{status} __**推薦度#{i+1}**__ | {r['game']} | **{r['pick']}**\n"
            msg += f"  └ Edge: `{r['edge']:.2%}` | 賠率: `{r['odds']}` | 倉: `{r['k']:.2%}`\n"
            
            if not is_qualified:
                msg += f"  └ *差 {gap:.2%} 達標* | "
            else:
                msg += "  └ "
            msg += f"*{r['insight']}*\n"
            msg += "---" # 分隔線

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
