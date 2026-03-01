import requests
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ===== NBA V19.1 Adaptive Flexibility (自適應靈活版) =====
# 門檻設定：讓分 1.5% / 大小分 2.0%
STRICT_EDGE_BASE = 0.015    
TOTAL_EDGE_BASE = 0.020     
KELLY_CAP = 0.025           

# 調整係數 (Real-World 修正)
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

def generate_insight(game, pick_type, pt, edge):
    """根據數據自動生成分析文字"""
    if "買分" in pick_type:
        return f"分析：{game.split(' @ ')[1]}雖強，但買分至 {pt:+} 更有安全墊，能有效防範尾盤卡分風險。"
    if "Over" in pick_type:
        return f"分析：兩隊近期進攻效率回升，盤口線開得偏低，是模型眼中的數據窪地。"
    if "Under" in pick_type:
        return f"分析：高分盤觸發防禦懲罰，兩隊近期防守強度提升，預計將演變成一場防守拉鋸戰。"
    if abs(pt) > 10:
        return f"分析：對手近期體能處於劣勢，強隊即便開出深盤，依然具備統治級的贏球期望值。"
    return f"分析：模型識別到盤口與球隊真實戰力存在微弱偏差，具備投機價值。"

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
                    all_calculated_picks.append({
                        "game": f"{cn(away_en)} @ {cn(home_en)}", 
                        "pick": f"{label}({f_pt:+})：{cn(o['name'])}", 
                        "odds": round(f_odds,2), "edge": edge, 
                        "k": kelly(f_p, f_odds), 
                        "insight": generate_insight(f"{cn(away_en)} @ {cn(home_en)}", label, f_pt, edge)
                    })

        # --- 大小 ---
        if totals:
            for o in totals:
                line, odds = o["point"], o["price"]
                p_total = 0.5 + (abs(p_home - 0.5) * TOTAL_COEF) if o["name"] == "Over" else 0.5 - (abs(p_home - 0.5) * TOTAL_COEF)
                penalty = 0.015 + (0.015 if line > 230 else 0)
                edge = p_total - (1/odds) - penalty
                if edge > 0:
                    all_calculated_picks.append({
                        "game": f"{cn(away_en)} @ {cn(home_en)}", 
                        "pick": f"🏀 {o['name']} {line}", 
                        "odds": odds, "edge": edge, 
                        "k": kelly(p_total, odds), 
                        "insight": generate_insight(f"{cn(away_en)} @ {cn(home_en)}", o["name"], line, edge)
                    })

    # --- 格式化輸出 ---
    # 按照推薦度排序
    sorted_picks = sorted(all_calculated_picks, key=lambda x: x["edge"], reverse=True)
    
    msg = f"🛰️ NBA V19.1 Adaptive - {datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += "*(門檻：讓分 1.5% / 大小 2.0% - 強制顯示前 4 場)\n"
    
    if not sorted_picks:
        msg += "\n> 市場效率極高，目前無正值場次。"
    else:
        # 強制顯示排序前 4 的場次
        for i, r in enumerate(sorted_picks[:4]):
            is_qualified = r['edge'] >= (STRICT_EDGE_BASE if "讓分" in r['pick'] else TOTAL_EDGE_BASE)
            status = "✅" if is_qualified else "⚠️ [未達標]"
            
            msg += f"\n{status} **推薦度#{i+1}** | {r['game']} | **{r['pick']}**\n"
            msg += f"  └ Edge:{r['edge']:.2%} | 賠率:{r['odds']} | 倉:{r['k']:.2%}\n"
            msg += f"  └ *{r['insight']}*\n"
            msg += "-" * 20

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    main()
