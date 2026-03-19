import requests
import os
import json
import random
import math
from datetime import datetime, timedelta

# ==========================================
# NBA V81.0 Deep Insight - Integrated Edition
# ==========================================

# 1. 環境變數設定 (請確保 GitHub Secrets 已設定)
API_KEY = os.getenv("ODDS_API_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"
DB_FILE = "nba_v81_db.json"

# 2. 核心分析參數
SIMS = 20000               # 蒙地卡羅模擬次數
EDGE_THRESHOLD = 0.035     # 領先市場 3.5% 才推薦
MODEL_WEIGHT = 0.45        # AI 模型預測佔比
MARKET_WEIGHT = 0.55       # 市場盤口共識佔比 (避險關鍵)
HOME_ADV = 2.4             # 主場優勢基本分
TIME_OFFSET = -10          # 顯示時間修正 (分鐘)

# 3. 資金管理設定 (本金 1000 元)
TOTAL_BANKROLL = 1000      
KELLY_FRACTION = 0.25      # 1/4 凱利 (穩健型)
MAX_SINGLE_BET = 150       # 單場上限 150 元

# 4. 靜態球隊數據庫 (作為分析基底)
TEAM_CN = {"Boston Celtics":"塞爾提克","Milwaukee Bucks":"公鹿","Denver Nuggets":"金塊","Golden State Warriors":"勇士","Los Angeles Lakers":"湖人","Phoenix Suns":"太陽","Dallas Mavericks":"獨行俠","Los Angeles Clippers":"快艇","Miami Heat":"熱火","Philadelphia 76ers":"七六人","New York Knicks":"尼克","Toronto Raptors":"暴龍","Chicago Bulls":"公牛","Atlanta Hawks":"老鷹","Brooklyn Nets":"籃網","Cleveland Cavaliers":"騎士","Indiana Pacers":"溜馬","Detroit Pistons":"活塞","Orlando Magic":"魔術","Charlotte Hornets":"黃蜂","Washington Wizards":"巫師","Houston Rockets":"火箭","San Antonio Spurs":"馬刺","Memphis Grizzlies":"灰熊","New Orleans Pelicans":"鵜鶘","Minnesota Timberwolves":"灰狼","Oklahoma City Thunder":"雷霆","Utah Jazz":"爵士","Sacramento Kings":"國王","Portland Trail Blazers":"拓荒者"}

TEAM_STATS = {
    "Boston Celtics": {"off":121,"def":110,"pace":99}, "Denver Nuggets": {"off":118,"def":112,"pace":97},
    "Oklahoma City Thunder": {"off":120,"def":111,"pace":101}, "Milwaukee Bucks": {"off":119,"def":113,"pace":100},
    "Minnesota Timberwolves": {"off":115,"def":108,"pace":97}, "Los Angeles Clippers": {"off":117,"def":112,"pace":98},
    "Dallas Mavericks": {"off":119,"def":114,"pace":100}, "Phoenix Suns": {"off":117,"def":113,"pace":99},
    "Golden State Warriors": {"off":118,"def":114,"pace":101}, "Los Angeles Lakers": {"off":116,"def":114,"pace":101},
    "New York Knicks": {"off":116,"def":111,"pace":95}, "Cleveland Cavaliers": {"off":115,"def":110,"pace":96},
    "Philadelphia 76ers": {"off":118,"def":113,"pace":97}, "Sacramento Kings": {"off":119,"def":115,"pace":101},
    "Miami Heat": {"off":112,"def":111,"pace":95}, "Indiana Pacers": {"off":122,"def":118,"pace":103},
    "Houston Rockets": {"off":114,"def":111,"pace":98}, "New Orleans Pelicans": {"off":116,"def":111,"pace":98},
    "Atlanta Hawks": {"off":118,"def":118,"pace":102}, "Chicago Bulls": {"off":111,"def":113,"pace":97},
    "Toronto Raptors": {"off":110,"def":115,"pace":100}, "Brooklyn Nets": {"off":111,"def":114,"pace":98},
    "Charlotte Hornets": {"off":108,"def":118,"pace":101}, "Detroit Pistons": {"off":109,"def":119,"pace":100},
    "Utah Jazz": {"off":112,"def":118,"pace":101}, "Portland Trail Blazers": {"off":108,"def":118,"pace":99},
    "San Antonio Spurs": {"off":112,"def":119,"pace":101}, "Washington Wizards": {"off":109,"def":120,"pace":102},
    "Memphis Grizzlies": {"off":113,"def":113,"pace":100}, "Orlando Magic": {"off":113,"def":110,"pace":97}
}

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE,"r") as f: return json.load(f)
        except: pass
    return {"history":{}, "locks": {}}

def predict_spread(home, away):
    h = TEAM_STATS.get(home, {"off":114,"def":114,"pace":100})
    a = TEAM_STATS.get(away, {"off":114,"def":114,"pace":100})
    expected_pace = (h["pace"] + a["pace"]) / 2
    pace_factor = expected_pace / 100
    h_net = h["off"] - h["def"]
    a_net = a["off"] - a["def"]
    return ((h_net - a_net) / 2) * pace_factor + HOME_ADV, pace_factor

def run():
    db = load_db()
    now_tw = datetime.utcnow() + timedelta(hours=8)
    
    try:
        r = requests.get(f"{BASE_URL}/odds/", params={"apiKey":API_KEY, "regions":"us", "markets":"spreads", "oddsFormat":"decimal"})
        r.raise_for_status()
        games = r.json()
    except Exception as e:
        print(f"API Error: {e}"); return

    grouped = {}

    for g in games:
        home, away = g["home_team"], g["away_team"]
        raw_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        if (raw_time - now_tw).total_seconds() > 86400 or (now_tw - raw_time).total_seconds() > 7200: continue
        
        date_str = raw_time.strftime("%m/%d (週%w)").replace("週0","週日").replace("週1","週一").replace("週2","週二").replace("週3","週三").replace("週4","週四").replace("週5","週五").replace("週6","週六")
        display_time = (raw_time + timedelta(minutes=TIME_OFFSET)).strftime("%H:%M")

        books = g.get("bookmakers", [])
        if not books: continue
        
        best_pick = None
        for o in books[0]["markets"][0]["outcomes"]:
            model_margin, pace_factor = predict_spread(home, away)
            line = o.get("point", 0)
            
            # 分析核心：混合模型預測與莊家開盤
            raw_target = model_margin if o["name"] == home else -model_margin
            vegas_target = -line 
            blended_target = (raw_target * MODEL_WEIGHT) + (vegas_target * MARKET_WEIGHT)
            
            # 蒙地卡羅模擬
            dynamic_std = 12.5 + (pace_factor * 1.5)
            win_count = sum(1 for _ in range(SIMS) if (blended_target + random.gauss(0, dynamic_std) + line) > 0)
            
            prob = win_count / SIMS
            edge = prob - (1/o["price"])
            ev = (prob * (o["price"] - 1)) - (1 - prob)

            if edge > EDGE_THRESHOLD:
                # 凱利公式計算注碼
                b_val = o["price"] - 1
                k_p = (b_val * prob - (1 - prob)) / b_val
                bet_amt = round(max(0, min(TOTAL_BANKROLL * k_p * KELLY_FRACTION, MAX_SINGLE_BET)))
                
                pick = {
                    "match": f"{TEAM_CN.get(away,away)} @ {TEAM_CN.get(home,home)}",
                    "team": TEAM_CN.get(o["name"], o["name"]), "line": line, "odds": o["price"],
                    "prob": prob, "ev": ev, "edge": edge, "bet": bet_amt, "time": display_time
                }
                if best_pick is None or ev > best_pick["ev"]: best_pick = pick

        if best_pick:
            grouped.setdefault(date_str, []).append(best_pick)

    # --- 訊息組裝 ---
    msg = f"🛡️ **NBA V81.0 Deep Insight**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n💰 運算本金：{TOTAL_BANKROLL} 元\n"
    
    if not grouped:
        msg += "\n📭 今日無符合「高偏差」條件場次。"
    else:
        for date, picks in grouped.items():
            msg += f"\n📅 **{date} 當日強勢精選**\n"
            picks.sort(key=lambda x: x['edge'], reverse=True)
            top_4 = picks[:4]
            for p in top_4:
                msg += f"**{p['match']}**\n✨ {p['time']} | {p['team']} {p['line'] :+} @ **{p['odds']}**\n"
                msg += f"📊 混合勝率: **{p['prob']:.1%}** | 領先: **{p['edge']:+.1%}**\n"
                msg += f"💵 **建議下注：{p['bet']} 元**\n----------------\n"
            
            if len(top_4) >= 2:
                msg += f"🔗 **今日精選串關 (1+1)\n✅ {top_4[0]['team']} & {top_4[1]['team']}\n💵 建議金額：50 元**\n"

    requests.post(WEBHOOK, json={"content": msg})

if __name__ == "__main__":
    run()
