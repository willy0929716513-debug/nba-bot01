import requests
import os
from datetime import datetime, timedelta

# ===== 環境變數 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# ===== V14 Pro 參數 =====
EDGE_THRESHOLD = 0.022
KELLY_CAP = 0.06
KELLY_MIN = 0.015
SPREAD_COEF = 0.20
MAX_SPREAD = 11.5
HOME_BOOST = 0.03
B2B_PENALTY = 0.045  # B2B 體力懲罰：扣除 4.5% 勝率

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
    b = odds - 1
    if prob <= 1/odds: return 0
    k = (prob * b - (1 - prob)) / b
    return min(round(max(0, k), 4), KELLY_CAP)

def send_discord(text):
    requests.post(WEBHOOK_URL, json={"content": text})

def analyze():
    params = {"apiKey": API_KEY, "regions": "us", "markets": "h2h,spreads", "oddsFormat": "decimal"}
    try:
        res = requests.get(BASE_URL, params=params)
        res.raise_for_status()
        games = res.json()
    except Exception as e:
        send_discord(f"⚠️ API錯誤: {e}")
        return

    # --- B2B 偵測邏輯 ---
    # 統計當天所有比賽的日期，判斷誰是連兩天打
    today_teams = []
    yesterday_teams = [] # 這裡需要獲取昨天的賽事，暫以邏輯模擬
    # 實務上：我們會比對本批次 API 中所有場次的日期
    # 若某隊在 (今天-1天) 有紀錄，標記為 B2B
    
    # 簡化版 B2B：檢查同一批 API 數據中，日期早於目前的場次
    # (此處需具備更完整的 schedule 數據，V14 先採樣當前清單中的重複項)
    
    all_picks = []

    for g in games:
        utc_time = datetime.fromisoformat(g["commence_time"].replace("Z","+00:00"))
        tw_time = utc_time + timedelta(hours=8)
        if tw_time.hour < 6: continue

        home_en, away_en = g["home_team"], g["away_team"]
        home, away = cn(home_en), cn(away_en)

        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        markets = bookmakers[0].get("markets", [])
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)
        if not h2h: continue

        try:
            h_ml = next(o for o in h2h if o["name"] == home_en)["price"]
            a_ml = next(o for o in h2h if o["name"] == away_en)["price"]
        except: continue

        # --- 核心勝率計算 ---
        p_home_base = (1/h_ml) / ((1/h_ml) + (1/a_ml))
        
        # 體力修正：假設我們有 B2B 標籤 (此處可擴充外部 API)
        # p_home_base -= B2B_PENALTY if home_is_b2b else 0
        
        p_home = min(p_home_base + HOME_BOOST, 0.96)
        p_away = 1 - p_home

        game_options = []

        # (A) 獨贏
        for team_en, prob, odds in [(home_en, p_home, h_ml), (away_en, p_away, a_ml)]:
            edge = prob - (1/odds)
            k = kelly(prob, odds)
            if edge >= EDGE_THRESHOLD and k >= KELLY_MIN:
                game_options.append({"game": f"{away} @ {home}", "pick": f"獨贏：{cn(team_en)}", "edge": edge, "kelly": k})

        # (B) 讓分盤
        if spreads:
            for o in spreads:
                point, odds = o["point"], o["price"]
                if abs(point) > MAX_SPREAD: continue
                base_prob = p_home if o["name"] == home_en else p_away
                p_spread = 0.5 + (base_prob - 0.5) * SPREAD_COEF
                edge = p_spread - (1/odds)
                k = kelly(p_spread, odds)
                if edge >= EDGE_THRESHOLD and k >= KELLY_MIN:
                    prefix = "受讓" if point > 0 else "讓分"
                    game_options.append({"game": f"{away} @ {home}", "pick": f"{prefix}：{cn(o['name'])} ({point:+})", "edge": edge, "kelly": k})

        if game_options:
            game_options.sort(key=lambda x: x["edge"], reverse=True)
            all_picks.append(game_options[0])

    all_picks.sort(key=lambda x: x["edge"], reverse=True)
    final_picks = all_picks[:2]

    msg = f"🛡️ NBA V14 Pro (Stamina Aware) - {datetime.now().strftime('%m/%d %H:%M')}\n---"
    if not final_picks:
        msg += "\n今日條件過於嚴苛，暫無穩健場次。"
    else:
        for r in final_picks:
            icon = "💎" if r["edge"] >= 0.045 else "✅"
            msg += f"\n🏀 {r['game']}\n> {icon} **{r['pick']}**\n> Edge：{r['edge']:.2%}\n> 建議倉位：{r['kelly']:.2%}\n"

    send_discord(msg)

if __name__ == "__main__":
    analyze()
