import requests
import os
from datetime import datetime

# ===== 環境變數 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# ===== 中文隊名 (保持原樣) =====
TEAM_CN = {
    "Los Angeles Lakers": "湖人", "Golden State Warriors": "勇士", "Boston Celtics": "塞爾提克",
    "Milwaukee Bucks": "公鹿", "Denver Nuggets": "金塊", "Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽", "LA Clippers": "快艇", "Miami Heat": "熱火",
    "Philadelphia 76ers": "七六人", "Sacramento Kings": "國王", "New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼", "Dallas Mavericks": "獨行俠", "New York Knicks": "尼克",
    "Orlando Magic": "魔術", "Charlotte Hornets": "黃蜂", "Detroit Pistons": "活塞",
    "Toronto Raptors": "暴龍", "Chicago Bulls": "公牛", "San Antonio Spurs": "馬刺",
    "Utah Jazz": "爵士", "Brooklyn Nets": "籃網", "Atlanta Hawks": "老鷹",
    "Cleveland Cavaliers": "騎士", "Indiana Pacers": "溜馬", "Memphis Grizzlies": "灰熊",
    "Portland Trail Blazers": "拓荒者", "Washington Wizards": "巫師", "Houston Rockets": "火箭"
}

def kelly(prob, odds=1.91):
    """計算凱利值：(期望勝率 * 賠率 - 1) / (賠率 - 1)"""
    b = odds - 1
    if b <= 0: return 0
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 4))

def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        part = text[i:i+MAX]
        requests.post(WEBHOOK_URL, json={"content": part})

def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    try:
        res = requests.get("https://api.the-odds-api.com/v4/sports/basketball_nba/odds", params=params)
        res.raise_for_status()
        games = res.json()
    except Exception as e:
        send_discord(f"API錯誤: {e}")
        return

    recommend_text = f"**🔥 NBA 最優投資推薦 (V9 穩定版) - {datetime.now().strftime('%m/%d')}**\n"
    recommend_text += "*(模型已修正：提升受讓權重、壓抑虛高信心值)*\n---"
    has_recommend = False

    for g in games:
        home_en, away_en = g["home_team"], g["away_team"]
        home, away = TEAM_CN.get(home_en, home_en), TEAM_CN.get(away_en, away_en)

        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue
        
        markets = bookmakers[0].get("markets", [])
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)

        if not h2h: continue

        # 1. 計算基礎市場機率 (去水後)
        try:
            h_ml = next(o for o in h2h if o["name"] == home_en)["price"]
            a_ml = next(o for o in h2h if o["name"] == away_en)["price"]
            p_home_base = (1/h_ml) / ((1/h_ml) + (1/a_ml))
        except: continue

        # 2. 模擬修正 (主場優勢 + 近況微調)
        p_home_final = min(p_home_base + 0.03, 0.96)
        p_away_final = 1 - p_home_final

        # 3. 多維度評估最優選
        options = []

        # (A) 主客勝獨贏
        options.append({"name": f"獨贏：{home}", "score": kelly(p_home_final, h_ml)})
        options.append({"name": f"獨贏：{away}", "score": kelly(p_away_final, a_ml)})

        # (B) 讓分/受讓盤
        if spreads:
            try:
                h_spread_data = next(o for o in spreads if o["name"] == home_en)
                h_pt = h_spread_data["point"]
                h_sp_price = h_spread_data["price"]
                
                # 修正：縮小 ML 勝率與 Spread 勝率的關聯 (從 0.7 降至 0.25)
                # 這是為了預防「強隊必過盤」的數學陷阱
                p_h_spread = 0.5 + (p_home_final - 0.5) * 0.25
                
                # 主隊側評分
                h_label = f"{'受讓' if h_pt > 0 else '讓分'}：{home} ({h_pt:+})"
                h_score = kelly(p_h_spread, h_sp_price)
                if h_pt > 0: h_score += 0.01  # 給予受讓 1% 穩定度加權
                options.append({"name": h_label, "score": h_score})

                # 客隊側評分
                a_sp_price = next(o for o in spreads if o["name"] == away_en)["price"]
                a_label = f"{'受讓' if h_pt < 0 else '讓分'}：{away} ({-h_pt:+})"
                a_score = kelly(1 - p_h_spread, a_sp_price)
                if h_pt < 0: a_score += 0.01  # 給予受讓 1% 穩定度加權
                options.append({"name": a_label, "score": a_score})
            except: pass

        # 4. 只挑選該場最強訊號 (唯一)
        options.sort(key=lambda x: x["score"], reverse=True)
        best = options[0]

        # 門檻：凱利值 > 0.04 才推薦
        if best["score"] > 0.04:
            has_recommend = True
            icon = "💎" if best["score"] > 0.1 else "✅"
            recommend_text += f"\n**{away} @ {home}**\n"
            recommend_text += f"> {icon} **最優選擇：{best['name']}**\n"
            recommend_text += f"> 信心係數：{best['score']:.2%}\n"

    if not has_recommend:
        recommend_text += "\n今日暫無高價值投注標的，建議觀望。"

    send_discord(recommend_text)

if __name__ == "__main__":
    analyze()
