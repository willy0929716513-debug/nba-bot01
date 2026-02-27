import requests
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ===== V15.6 Reality Check 參數 =====
EDGE_THRESHOLD = 0.022      # 回歸 2.2% 門檻
KELLY_CAP = 0.05            # 倉位上限維持 5%
SPREAD_COEF = 0.16          # 進一步調降讓分敏感度，追求更真實的勝率
ODDS_MIN, ODDS_MAX = 1.45, 3.20

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

def get_rank_info(edge):
    if edge >= 0.045: return "💎 鑽石級 (S)", "🔥"
    if edge >= 0.032: return "🔥 推薦級 (A)", "⭐"
    return "✅ 穩健級 (B)", "▫️"

def kelly(prob, odds):
    b = odds - 1
    if prob <= 1/odds: return 0
    return min(round(max(0, (prob * b - (1 - prob)) / b), 4), KELLY_CAP)

def analyze():
    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"})
        games = res.json()
    except: return

    dated_picks = defaultdict(list)

    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        date_str = tw_time.strftime('%m/%d (週%w)').replace('週0','週日').replace('週1','週一').replace('週2','週二').replace('週3','週三').replace('週4','週四').replace('週5','週五').replace('週6','週六')

        home_en, away_en = g["home_team"], g["away_team"]
        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        m_list = bookmakers[0].get("markets", [])
        h2h = next((m["outcomes"] for m in m_list if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in m_list if m["key"] == "spreads"), None)
        if not h2h: continue

        h_ml = next(o for o in h2h if o["name"] == home_en)["price"]
        a_ml = next(o for o in h2h if o["name"] == away_en)["price"]
        p_home = min((1/h_ml) / ((1/h_ml) + (1/a_ml)) + 0.02, 0.95)
        p_away = 1 - p_home

        game_candidates = []
        # (A) 獨贏
        for t_en, prob, odds in [(home_en, p_home, h_ml), (away_en, p_away, a_ml)]:
            edge = prob - (1/odds)
            if edge >= EDGE_THRESHOLD and ODDS_MIN <= odds <= ODDS_MAX:
                game_candidates.append({"pick": f"獨贏：{cn(t_en)}", "odds": odds, "edge": edge, "prob": prob})

        # (B) 讓分盤 (引入階梯懲罰)
        if spreads:
            for o in spreads:
                point, odds = o["point"], o["price"]
                
                # --- 階梯式讓分懲罰邏輯 ---
                abs_pt = abs(point)
                if abs_pt > 15: penalty = 0.045
                elif abs_pt > 10.5: penalty = 0.025
                elif abs_pt > 6.5: penalty = 0.010
                else: penalty = 0
                
                if ODDS_MIN <= odds <= ODDS_MAX:
                    p_spread = 0.5 + ((p_home if o["name"] == home_en else p_away) - 0.5) * SPREAD_COEF
                    edge = p_spread - (1/odds) - penalty
                    
                    if edge >= EDGE_THRESHOLD:
                        prefix = "受讓" if point > 0 else "讓分"
                        game_candidates.append({
                            "pick": f"{prefix}：{cn(o['name'])} ({point:+})",
                            "odds": odds, "edge": edge, "prob": p_spread
                        })

        if game_candidates:
            game_candidates.sort(key=lambda x: x["edge"], reverse=True)
            best = game_candidates[0]
            dated_picks[date_str].append({
                "game": f"{cn(away_en)} @ {cn(home_en)}",
                "pick": best["pick"], "odds": best["odds"], "edge": best["edge"], "kelly": kelly(best["prob"], best["odds"])
            })

    # 輸出格式化
    msg = f"🛡️ NBA V15.6 Reality Check - {datetime.now().strftime('%m/%d %H:%M')}\n"
    msg += f"*(實戰優化：已實施階梯式深盤懲罰，過濾高風險垃圾時間場次)*\n"

    for date in sorted(dated_picks.keys()):
        msg += f"\n📅 **{date}**\n"
        picks = sorted(dated_picks[date], key=lambda x: x["edge"], reverse=True)
        for r in picks:
            rank, emoji = get_rank_info(r["edge"])
            msg += f"> {emoji} **{r['pick']}** | {r['game']}\n"
            msg += f"> 賠率：{r['odds']:.2f} | 修正優勢：{r['edge']:.2%} | 倉位：{r['kelly']:.2%}\n"

    requests.post(WEBHOOK_URL, json={"content": msg})

if __name__ == "__main__":
    analyze()
