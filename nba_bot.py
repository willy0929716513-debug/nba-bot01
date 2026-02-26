import requests
import os
from datetime import datetime

# ===== 環境變數設定 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# ===== 中文隊名對照表 =====
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
    """計算凱利準則：(p*b - q)/b"""
    b = odds - 1
    if b <= 0: return 0
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 4))

def send_discord(text):
    """分段發送訊息至 Discord"""
    MAX_LEN = 1900
    for i in range(0, len(text), MAX_LEN):
        part = text[i:i+MAX_LEN]
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
        send_discord(f"⚠️ API 請求失敗: {e}")
        return

    recommend_text = f"**🏀 NBA 最優投資推薦 (V11 統計回歸版) - {datetime.now().strftime('%m/%d')}**\n"
    recommend_text += "> *修正重點：符號精準判定、深盤風險壓抑、勝率平滑化*\n---"
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

        # 1. 計算基礎市場機率 (隱含勝率)
        try:
            h_ml_price = next(o for o in h2h if o["name"] == home_en)["price"]
            a_ml_price = next(o for o in h2h if o["name"] == away_en)["price"]
            # 去除博彩公司抽水後的真實機率
            p_home_market = (1/h_ml_price) / ((1/h_ml_price) + (1/a_ml_price))
        except: continue

        # 2. 模擬修正 (加入主場優勢權重)
        p_home_final = min(p_home_market + 0.03, 0.95)
        p_away_final = 1 - p_home_final

        # 3. 評估所有投注選項
        options = []

        # (A) 獨贏選項 (Moneyline)
        options.append({"name": f"獨贏：{home}", "score": kelly(p_home_final, h_ml_price)})
        options.append({"name": f"獨贏：{away}", "score": kelly(p_away_final, a_ml_price)})

        # (B) 讓分盤選項 (Spread)
        if spreads:
            try:
                h_spread_data = next(o for o in spreads if o["name"] == home_en)
                h_pt = h_spread_data["point"]      # 主隊讓分點數
                h_sp_price = h_spread_data["price"]
                
                # --- V11 關鍵修正：讓分盤勝率平滑化 ---
                # 即使 ML 預測很強，讓分過盤率也應控制在 50% 附近波動
                p_h_spread = 0.5 + (p_home_final - 0.5) * 0.1
                
                # 主隊標籤判定
                h_type = "受讓" if h_pt > 0 else "讓分"
                h_label = f"{h_type}：{home} ({h_pt:+})"
                h_score = kelly(p_h_spread, h_sp_price)
                if h_pt > 10: h_score *= 0.5 # 深盤受讓保護降低
                if h_pt < -10: h_score *= 0.5 # 深讓分風險壓抑
                options.append({"name": h_label, "score": h_score})

                # 客隊標籤判定
                a_sp_price = next(o for o in spreads if o["name"] == away_en)["price"]
                a_pt = -h_pt
                a_type = "受讓" if a_pt > 0 else "讓分"
                a_label = f"{a_type}：{away} ({a_pt:+})"
                a_score = kelly(1 - p_h_spread, a_sp_price)
                if a_pt > 10: a_score *= 0.5
                if a_pt < -10: a_score *= 0.5
                options.append({"name": a_label, "score": a_score})
            except: pass

        # 4. 挑選單場最優選
        options.sort(key=lambda x: x["score"], reverse=True)
        best = options[0]

        # 門檻：只有信心係數 > 4.5% 才推薦 (過濾掉開盤太準的比賽)
        if best["score"] > 0.045:
            has_recommend = True
            icon = "💎" if best["score"] > 0.1 else "✅"
            recommend_text += f"\n**{away} @ {home}**\n"
            recommend_text += f"> {icon} **最優選擇：{best['name']}**\n"
            recommend_text += f"> 信心係數：{best['score']:.2%}\n"

    if not has_recommend:
        recommend_text += "\n今日盤口極為精準，無明顯獲利空間，建議觀望。"

    send_discord(recommend_text)

if __name__ == "__main__":
    print(f"[{datetime.now()}] 執行分析中...")
    analyze()
