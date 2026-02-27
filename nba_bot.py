import requests
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ===== V15.8 Insight 參數 =====
EDGE_THRESHOLD = 0.018      # 稍微放寬門檻以增加掃描廣度
KELLY_CAP = 0.05
SPREAD_COEF = 0.20          # 恢復適中的敏感度
ODDS_MIN, ODDS_MAX = 1.35, 3.20

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

def analyze():
    try:
        res = requests.get(BASE_URL, params={"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"})
        games = res.json()
    except Exception as e:
        print(f"API Error: {e}")
        return

    all_picks = []
    insights = [] # 儲存未達標但有價值的資訊

    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        date_str = tw_time.strftime('%m/%d (週%w)')

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

        # 掃描邏輯
        candidates = []
        # 獨贏
        for t_en, prob, odds in [(home_en, p_home, h_ml), (away_en, p_away, a_ml)]:
            edge = prob - (1/odds)
            if edge > 0.005: # 只要有優勢就記錄
                candidates.append({"type": "獨贏", "team": cn(t_en), "odds": odds, "edge": edge, "prob": prob})

        # 讓分
        if spreads:
            for o in spreads:
                point, odds = o["point"], o["price"]
                abs_pt = abs(point)
                penalty = 0.045 if abs_pt > 15 else (0.015 if abs_pt >= 8.5 else 0)
                p_spread = 0.5 + ((p_home if o["name"] == home_en else p_away) - 0.5) * SPREAD_COEF
                edge = p_spread - (1/odds) - penalty
                if edge > 0.005:
                    candidates.append({"type": f"{'受讓' if point > 0 else '讓分'}({point:+})", "team": cn(o['name']), "odds": odds, "edge": edge, "prob": p_spread})

        if candidates:
            candidates.sort(key=lambda x: x["edge"], reverse=True)
            best = candidates[0]
            pick_info = {
                "game": f"{cn(away_en)} @ {cn(home_en)}",
                "date": date_str,
                "pick": f"{best['type']}：{best['team']}",
                "odds": best["odds"],
                "edge": best["edge"],
                "prob": best["prob"]
            }
            # 判斷是否達標
            if best["edge"] >= EDGE_THRESHOLD and ODDS_MIN <= best["odds"] <= ODDS_MAX:
                all_picks.append(pick_info)
            else:
                insights.append(pick_info)

    # 輸出組合
    msg = f"👁️ NBA V15.8 Insight - {datetime.now().strftime('%m/%d %H:%M')}\n"
    
    if all_picks:
        msg += "\n🎯 **精選推薦 (達標場次)**"
        for r in sorted(all_picks, key=lambda x: x["edge"], reverse=True):
            msg += f"\n📅 {r['date']} | **{r['game']}**\n> 💰 {r['pick']} | 賠率：{r['odds']:.2f}\n> 📈 優勢：{r['edge']:.2%} | 倉位：{kelly(r['prob'], r['odds']):.2%}"
    else:
        msg += "\n🚫 今日無達標場次。"

    if insights:
        msg += "\n\n🔍 **觀察名單 (優勢不足或賠率過低)**"
        for r in sorted(insights, key=lambda x: x["edge"], reverse=True)[:3]: # 只列前三場
            reason = "賠率過低" if r["odds"] < ODDS_MIN else "優勢未達 1.8%"
            msg += f"\n> {r['game']}：{r['pick']} (Edge: {r['edge']:.1%}, {reason})"

    requests.post(WEBHOOK_URL, json={"content": msg})

def kelly(prob, odds):
    b = odds - 1
    if prob <= 1/odds: return 0
    return min(round(max(0, (prob * b - (1 - prob)) / b), 4), KELLY_CAP)

if __name__ == "__main__":
    analyze()
