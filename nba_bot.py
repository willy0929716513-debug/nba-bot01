import requests
import os
import json
import math
from datetime import datetime, timedelta

# ==========================================
# NBA V87.2 Quantum Sync - Full Engine
# ==========================================

# 請確保已在環境變數中設置以下金鑰
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"

# --- 基礎數據與翻譯庫 ---
TEAM_CN = {
    "Boston Celtics":"塞爾提克", "Milwaukee Bucks":"公鹿", "Denver Nuggets":"金塊",
    "Golden State Warriors":"勇士", "Los Angeles Lakers":"湖人", "Phoenix Suns":"太陽",
    "Dallas Mavericks":"獨行俠", "Los Angeles Clippers":"快艇", "Miami Heat":"熱火",
    "Philadelphia 76ers":"七六人", "New York Knicks":"尼克", "Toronto Raptors":"暴龍",
    "Chicago Bulls":"公牛", "Atlanta Hawks":"老鷹", "Brooklyn Nets":"籃網",
    "Cleveland Cavaliers":"騎士", "Indiana Pacers":"溜馬", "Detroit Pistons":"活塞",
    "Orlando Magic":"魔術", "Charlotte Hornets":"黃蜂", "Washington Wizards":"巫師",
    "Houston Rockets":"火箭", "San Antonio Spurs":"馬刺", "Memphis Grizzlies":"灰熊",
    "New Orleans Pelicans":"鵜鶘", "Minnesota Timberwolves":"灰狼", "Oklahoma City Thunder":"雷霆",
    "Utah Jazz":"爵士", "Sacramento Kings":"國王", "Portland Trail Blazers":"拓荒者"
}

# 基礎攻防數值 (建議根據最新賽季數據定期更新)
TEAM_STATS = {
    "Boston Celtics": {"off":121,"def":110,"pace":99}, "Denver Nuggets": {"off":118,"def":112,"pace":97},
    "Oklahoma City Thunder": {"off":120,"def":111,"pace":101}, "Milwaukee Bucks": {"off":119,"def":113,"pace":100},
    "Minnesota Timberwolves": {"off":115,"def":108,"pace":97}
}

def get_prob(model_val, line, std):
    """計算常態分佈下的模型預測勝率"""
    z = (-line - model_val) / std
    return 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))

def run_sync_engine():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    print(f"[{now_tw.strftime('%Y-%m-%d %H:%M:%S')}] 啟動 Quantum Sync 引擎...")

    # 1. 抓取即時賠率
    r = requests.get(f"{BASE_URL}/odds/", params={
        "apiKey": API_KEY, "regions": "us,eu", "markets": "spreads", "oddsFormat": "decimal"
    })
    
    if r.status_code != 200:
        print("API 請求失敗"); return
    
    games = r.json()
    all_picks = []
    date_groups = {}

    # 2. 數據運算
    for g in games:
        home, away = g["home_team"], g["away_team"]
        commence = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        date_key = commence.strftime("%m/%d")
        
        # 取得隊伍數據
        h_s = TEAM_STATS.get(home, {"off":114, "def":114, "pace":99})
        a_s = TEAM_STATS.get(away, {"off":114, "def":114, "pace":99})

        # 模型預算讓分數
        spread_model = ((h_s["off"]-h_s["def"]) - (a_s["off"]-a_s["def"])) / 2 + 2.5 
        
        # 尋找最佳賠率莊家
        best_spread = {"price": 0, "point": 0, "book": "", "name": ""}
        for book in g.get("bookmakers", []):
            market = next((m for m in book["markets"] if m["key"] == "spreads"), None)
            if market:
                for outcome in market["outcomes"]:
                    if outcome["price"] > best_spread["price"]:
                        best_spread = {
                            "price": outcome["price"], "point": outcome["point"],
                            "book": book["title"], "name": outcome["name"]
                        }

        # 3. 計算勝率與 Edge
        target_val = spread_model if best_spread["name"] == home else -spread_model
        model_prob = get_prob(target_val, best_spread["point"], 12.5)
        implied_prob = 1 / best_spread["price"]
        edge = model_prob - implied_prob
        ev = (model_prob * best_spread["price"]) - 1

        if edge > 0.05: # 僅記錄 Edge > 5% 的優質標的
            pick_info = {
                "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                "pick": f"{TEAM_CN.get(best_spread['name'], best_spread['name'])} {best_spread['point']:+}",
                "time": commence.strftime("%H:%M"),
                "odds": best_spread["price"],
                "book": best_spread["book"],
                "model_prob": model_prob,
                "implied_prob": implied_prob,
                "edge": edge,
                "ev": ev,
                "date": date_key,
                "is_live": "🔴 [場中]" if edge > 0.20 else "" # 高 Edge 標註為場中關注
            }
            all_picks.append(pick_info)
            date_groups.setdefault(date_key, []).append(pick_info)

    # 4. 構建 V80.1 風格訊息
    msg = f"🛡️ **NBA V87.2 Quantum Sync**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    
    for date, picks in date_groups.items():
        msg += f"\n📅 **{date} ({get_weekday(date)})**\n"
        for p in picks:
            msg += f"{p['is_live']} 🔗 **{p['match']}** [受讓]\n"
            msg += f"✨ ⏰ {p['time']} | 🎯 {p['pick']} @ **{p['odds']}** ({p['book']})\n"
            msg += f"💰 EV: **{p['ev']:+.2f}** | CLV:  **↔️ 0.00%**\n"
            msg += f"📊 勝率: 模型 **{p['model_prob']:.1%}** vs 市場 **{p['implied_prob']:.1%}**\n"
            msg += f"📈 領先 (Edge): **{p['edge']:+.2%}\n"
            msg += "--------\n"

    # 5. AI 串關推薦 (取今日最強兩場)
    today_picks = date_groups.get(now_tw.strftime("%m/%d"), [])
    if len(today_picks) >= 2:
        today_picks.sort(key=lambda x: x["edge"], reverse=True)
        p1, p2 = today_picks[0], today_picks[1]
        msg += f"\n💎 **AI 推薦串關 (組合賠率 {p1['odds']*p2['odds']:.2f})**\n"
        msg += f"✅ {p1['match']} [{p1['pick']}]\n"
        msg += f"✅ {p2['match']} [{p2['pick']}]"

    # 發送到 Discord
    requests.post(WEBHOOK, json={"content": msg})

def get_weekday(date_str):
    days = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    # 假設為今年
    dt = datetime.strptime(f"{datetime.now().year}/{date_str}", "%Y/%m/%d")
    return days[dt.weekday()]

if __name__ == "__main__":
    run_sync_engine()
