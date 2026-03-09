import requests
import os
import json
import math
import random
from datetime import datetime, timedelta

# ==========================================
# NBA V86.1 Quantum Alpha - Pure Value Picker
# ==========================================

# 環境變數與路徑
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
DB_FILE = "nba_v86_db.json"

# --- 核心量化參數 (量化交易級設定) ---
MIN_EDGE = 0.04          # 領先門檻 4% (過濾雜訊)
TIME_OFFSET = -10        # 提前 10 分鐘對齊轉播
SPREAD_STD = 12.8        # 讓分盤標準差
TOTAL_STD = 13.5         # 大小分標準差
# ----------------------------------

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

# 動態攻防數據 (建議每日根據傷病調整)
TEAM_STATS = {
    "Boston Celtics": {"off":121,"def":110,"pace":99}, "Denver Nuggets": {"off":118,"def":112,"pace":97},
    "Oklahoma City Thunder": {"off":120,"def":111,"pace":101}, "Milwaukee Bucks": {"off":119,"def":113,"pace":100},
    "Minnesota Timberwolves": {"off":115,"def":108,"pace":97}, "Dallas Mavericks": {"off":119,"def":114,"pace":100},
    "Phoenix Suns": {"off":117,"def":113,"pace":99}, "Golden State Warriors": {"off":118,"def":114,"pace":101},
    "Los Angeles Lakers": {"off":116,"def":114,"pace":101}, "New York Knicks": {"off":116,"def":111,"pace":95},
    "Cleveland Cavaliers": {"off":115,"def":110,"pace":96}, "Philadelphia 76ers": {"off":118,"def":113,"pace":97}
}

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE,"r") as f: return json.load(f)
    return {"history":{}}

def save_db(db):
    with open(DB_FILE,"w") as f: json.dump(db, f, indent=2)

def get_prob(model_val, line, std):
    """ 使用誤差函數計算正態分佈累積機率 (代替蒙地卡羅) """
    z = (-line - model_val) / std
    return 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))

def kelly(prob, odds):
    """ 凱利公式控管倉位：(bp - q) / b """
    b = odds - 1
    k = (b * prob - (1 - prob)) / b
    return max(0, min(k * 0.25, 0.035)) # 四分之一凱利，單場上限 3.5%

def run():
    db = load_db()
    history = db.get("history", {})
    now_tw = datetime.utcnow() + timedelta(hours=8)
    
    r = requests.get(f"{BASE_URL}/odds/", params={"apiKey":API_KEY, "regions":"us", "markets":"spreads,totals", "oddsFormat":"decimal"})
    if r.status_code != 200: return
    games = r.json()

    all_opportunities = []

    for g in games:
        gid, home, away = g["id"], g["home_team"], g["away_team"]
        raw_commence = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        sync_time = raw_commence + timedelta(minutes=TIME_OFFSET)
        
        # 過濾已開賽過久的場次
        if now_tw > (sync_time + timedelta(minutes=150)): continue

        h_s = TEAM_STATS.get(home, {"off":113, "def":113, "pace":99})
        a_s = TEAM_STATS.get(away, {"off":113, "def":113, "pace":99})

        # --- 模型核心計算 ---
        spread_model = ((h_s["off"]-h_s["def"]) - (a_s["off"]-a_s["def"])) / 2 + 2.4
        total_model = ((h_s["pace"] + a_s["pace"]) / 2) * (h_s["off"] + a_s["off"]) / 100

        for book in g.get("bookmakers", []):
            for market in book["markets"]:
                for o in market["outcomes"]:
                    price, line = o["price"], o["point"]
                    hist_id = f"{gid}_{market['key']}_{o.get('name', 'total')}"
                    
                    # CLV 追蹤邏輯
                    if hist_id not in history: history[hist_id] = price
                    clv = (price - history[hist_id]) / history[hist_id]
                    
                    if market["key"] == "spreads":
                        target = spread_model if o["name"] == home else -spread_model
                        prob = get_prob(target, line, SPREAD_STD)
                        m_type = "讓分盤"
                    else: # totals
                        prob_over = get_prob(total_model, -line, TOTAL_STD)
                        prob = prob_over if o["name"] == "Over" else (1 - prob_over)
                        m_type = "大小分"

                    edge = prob - (1/price)

                    # 背離偵測 (Sentinel 邏輯)
                    if edge > 0.15 and clv < -0.015:
                        continue # 異常波動，跳過此標的

                    if edge > MIN_EDGE:
                        all_opportunities.append({
                            "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                            "pick": f"{TEAM_CN.get(o['name'], o['name'])} {line:+}",
                            "type": m_type, "prob": prob, "edge": edge, "odds": price,
                            "stake": kelly(prob, price), "time": sync_time.strftime("%H:%M"),
                            "date": sync_time.strftime("%m/%d"), "clv": clv
                        })

    # --- 全市場價值排序 ---
    all_opportunities.sort(key=lambda x: x["edge"], reverse=True)

    message = f"🛡️ **NBA V86.1 Quantum Alpha**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    
    if not all_opportunities:
        message += "\n📭 目前市場賠率與模型預期高度一致，無投資價值。"
    else:
        # 只輸出前 5 名最優價值場次
        for opp in all_opportunities[:5]:
            clv_icon = "📈" if opp['clv'] > 0 else ("📉" if opp['clv'] < 0 else "↔️")
            message += f"\n💎 **{opp['match']}** ({opp['type']})\n"
            message += f"🎯 {opp['pick']} @ **{opp['odds']}** | {opp['time']}\n"
            message += f"📊 預估勝率: **{opp['prob']:.1%}** | CLV: {clv_icon}\n"
            message += f"📈 獲利優勢 (Edge): **{opp['edge']:+.2%}**\n"
            message += f"💰 建議投入: **{opp['stake']:.2%}** 本金\n"
            message += "--------"

    requests.post(WEBHOOK, json={"content": message})
    save_db({"history": history})

if __name__ == "__main__":
    run()
