import requests
import os
from datetime import datetime

# ===== 環境變數 =====
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# ===== 中文隊名對照表 (保持不變) =====
TEAM_CN = {
    "Los Angeles Lakers": "湖人", "Golden State Warriors": "勇士",
    "Boston Celtics": "塞爾提克", "Milwaukee Bucks": "公鹿",
    "Denver Nuggets": "金塊", "Oklahoma City Thunder": "雷霆",
    "Phoenix Suns": "太陽", "LA Clippers": "快艇",
    "Miami Heat": "熱火", "Philadelphia 76ers": "七六人",
    "Sacramento Kings": "國王", "New Orleans Pelicans": "鵜鶘",
    "Minnesota Timberwolves": "灰狼", "Dallas Mavericks": "獨行俠",
    "New York Knicks": "尼克", "Orlando Magic": "魔術",
    "Charlotte Hornets": "黃蜂", "Detroit Pistons": "活塞",
    "Toronto Raptors": "暴龍", "Chicago Bulls": "公牛",
    "San Antonio Spurs": "馬刺", "Utah Jazz": "爵士",
    "Brooklyn Nets": "籃網", "Atlanta Hawks": "老鷹",
    "Cleveland Cavaliers": "騎士", "Indiana Pacers": "溜馬",
    "Memphis Grizzlies": "灰熊", "Portland Trail Blazers": "拓荒者",
    "Washington Wizards": "巫師", "Houston Rockets": "火箭"
}

def send_discord(text):
    MAX = 1900
    for i in range(0, len(text), MAX):
        part = text[i:i+MAX]
        requests.post(WEBHOOK_URL, json={"content": part})

def kelly(prob, odds=1.91):
    """計算凱利準則值，作為推薦強度的依據"""
    b = odds - 1
    if b <= 0: return 0
    k = (prob * b - (1 - prob)) / b
    return max(0, round(k, 4))

def analyze():
    params = {
        "apiKey": API_KEY,
        "regions": "us",
        "markets": "h2h,spreads",
        "oddsFormat": "decimal"
    }

    try:
        res = requests.get(f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds", params=params)
        res.raise_for_status()
        games = res.json()
    except Exception as e:
        send_discord(f"❌ API 錯誤: {e}")
        return

    recommend_text = f"**🏀 NBA 每日最優推薦 ({datetime.now().strftime('%m/%d')})**\n---"
    has_recommend = False

    for g in games:
        home_en, away_en = g["home_team"], g["away_team"]
        home, away = TEAM_CN.get(home_en, home_en), TEAM_CN.get(away_en, away_en)

        bookmakers = g.get("bookmakers", [])
        if not bookmakers: continue

        # 取得賠率數據
        markets = bookmakers[0].get("markets", [])
        h2h = next((m["outcomes"] for m in markets if m["key"] == "h2h"), None)
        spreads = next((m["outcomes"] for m in markets if m["key"] == "spreads"), None)

        if not h2h: continue

        # 1. 計算隱含勝率 (去水後的市場預期)
        try:
            h_price = next(o for o in h2h if o["name"] == home_en)["price"]
            a_price = next(o for o in h2h if o["name"] == away_en)["price"]
            p_home_market = (1/h_price) / ((1/h_price) + (1/a_price))
        except: continue

        # 2. 模擬修正 (主場加成與強隊權重)
        p_home_final = min(p_home_market + 0.035, 0.95) # 稍微看好主場
        p_away_final = 1 - p_home_final

        # 3. 評估所有投注選項的「信心得分」
        options = []

        # 選項 A: 主隊獨贏
        options.append({
            "name": f"獨贏：{home}",
            "score": kelly(p_home_final, h_price),
            "type": "ML"
        })

        # 選項 B: 客隊獨贏
        options.append({
            "name": f"獨贏：{away}",
            "score": kelly(p_away_final, a_price),
            "type": "ML"
        })

        # 選項 C: 讓分盤 (Spread)
        if spreads:
            try:
                h_spread_data = next(o for o in spreads if o["name"] == home_en)
                h_spread_val = h_spread_data["point"]
                h_spread_price = h_spread_data["price"]
                
                # 簡單估算讓分勝率：偏離 0.5 的程度隨 ML 勝率縮放
                # 如果主隊受讓 (+)，勝率會高於 50%
                p_home_spread = 0.5 + (p_home_final - 0.5) * 0.7 
                
                if h_spread_val > 0: # 主隊受讓，給予 2% 心理防禦加權
                    s_score = kelly(p_home_spread, h_spread_price) + 0.02
                    s_name = f"受讓：{home} ({h_spread_val:+})"
                else: # 主隊讓分
                    s_score = kelly(p_home_spread, h_spread_price)
                    s_name = f"讓分：{home} ({h_spread_val:+})"
                
                options.append({"name": s_name, "score": s_score, "type": "Spread"})
                
                # 客隊讓分/受讓對應邏輯
                a_spread_price = next(o for o in spreads if o["name"] == away_en)["price"]
                a_score = kelly(1 - p_home_spread, a_spread_price)
                a_name = f"{'受讓' if h_spread_val < 0 else '讓分'}：{away} ({-h_spread_val:+})"
                options.append({"name": a_name, "score": a_score, "type": "Spread"})
            except: pass

        # 4. 挑選該場「唯一」最高分選項
        options.sort(key=lambda x: x["score"], reverse=True)
        best = options[0]

        # 門檻：只有信心得分 > 0.05 (Kelly 5%) 才推薦
        if best["score"] > 0.05:
            has_recommend = True
            fire = "🔥" if best["score"] > 0.12 else "💡"
            recommend_text += f"\n**{away} @ {home}**\n"
            recommend_text += f"> 最優解：{fire} **{best['name']}**\n"
            recommend_text += f"> 信心度：{best['score']:.2%}\n"

    if not has_recommend:
        recommend_text += "\n今日盤口較硬，建議觀望。"

    send_discord(recommend_text)

if __name__ == "__main__":
    analyze()
