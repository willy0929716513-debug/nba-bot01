import requests
import os
import json
import random
from datetime import datetime, timedelta

# ==========================================
# NBA V84.2 Ultimate Accuracy - 整合修復版
# ==========================================

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
RAPID_API_KEY = os.getenv("X_RAPIDAPI_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"

# --- 核心分析參數 ---
SIMS = 25000               # 提高模擬次數增加穩定度
EDGE_THRESHOLD = 0.02      # 門檻 2% 
MODEL_WEIGHT = 0.45
MARKET_WEIGHT = 0.55
DYNAMIC_STD_BASE = 14.5    # 提高標準差防止勝率膨脹

# --- 1. 名稱正規化工具 (修復關鍵) ---
def clean_name(name):
    """將 'James, LeBron' 或 'L. James' 統一轉為小寫名姓格式"""
    if not name: return ""
    name = name.lower().replace("'", "").replace(".", "").replace("-", " ")
    if "," in name:
        parts = name.split(",")
        return f"{parts[1].strip()} {parts[0].strip()}"
    return name.strip()

def normalize_team(team_name):
    """統一球隊名稱對應"""
    for full_name in TEAM_STATS.keys():
        if team_name in full_name or full_name in team_name:
            return full_name
    return team_name

# --- 2. 數據庫 ---
TEAM_CN = {"Boston Celtics":"塞爾提克","Milwaukee Bucks":"公鹿","Denver Nuggets":"金塊","Golden State Warriors":"勇士","Los Angeles Lakers":"湖人","Phoenix Suns":"太陽","Dallas Mavericks":"獨行俠","Los Angeles Clippers":"快艇","Miami Heat":"熱火","Philadelphia 76ers":"七六人","New York Knicks":"尼克","Toronto Raptors":"暴龍","Chicago Bulls":"公牛","Atlanta Hawks":"老鷹","Brooklyn Nets":"籃網","Cleveland Cavaliers":"騎士","Indiana Pacers":"溜馬","Detroit Pistons":"活塞","Orlando Magic":"魔術","Charlotte Hornets":"黃蜂","Washington Wizards":"巫師","Houston Rockets":"火箭","San Antonio Spurs":"馬刺","Memphis Grizzlies":"灰熊","New Orleans Pelicans":"鵜鶘","Minnesota Timberwolves":"灰狼","Oklahoma City Thunder":"雷霆","Utah Jazz":"爵士","Sacramento Kings":"國王","Portland Trail Blazers":"拓荒者"}

IMPACT_PLAYERS = {
    "Boston Celtics": ["jayson tatum", "jaylen brown", "kristaps porzingis"],
    "Denver Nuggets": ["nikola jokic", "jamal murray"],
    "Milwaukee Bucks": ["giannis antetokounmpo", "damian lillard"],
    "Dallas Mavericks": ["luka doncic", "kyrie irving"],
    "Philadelphia 76ers": ["joel embiid", "tyrese maxey"],
    "Los Angeles Lakers": ["lebron james", "anthony davis"],
    "Golden State Warriors": ["stephen curry"],
    "Phoenix Suns": ["kevin durant", "devin bootker"],
    "Minnesota Timberwolves": ["anthony edwards", "karl anthony towns"],
    "Oklahoma City Thunder": ["shai gilgeous alexander", "chet holmgren"],
    "Sacramento Kings": ["dearon fox", "domantas sabonis"],
    "New York Knicks": ["jalen brunson"],
    "Indiana Pacers": ["tyrese haliburton"]
}

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

# --- 3. 傷病偵測功能 ---
def get_injury_report():
    url = "https://nba-injury-reports.p.rapidapi.com/"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "nba-injury-reports.p.rapidapi.com"}
    injured_dict = {}
    try:
        response = requests.get(url, headers=headers, timeout=12)
        data = response.json()
        for item in data:
            status = item.get("status", "").lower()
            if any(s in status for s in ["out", "questionable", "doubtful"]):
                team = normalize_team(item.get("team", ""))
                player = clean_name(item.get("player", ""))
                if team not in injured_dict: injured_dict[team] = []
                injured_dict[team].append(player)
        return injured_dict
    except: return {}

# --- 4. 預測核心 ---
def predict_margin(home, away, injury_data):
    h = TEAM_STATS.get(home, {"off":114,"def":114,"pace":100}).copy()
    a = TEAM_STATS.get(away, {"off":114,"def":114,"pace":100}).copy()
    
    h_missing = [p for p in IMPACT_PLAYERS.get(home, []) if any(p in injured for injured in injury_data.get(home, []))]
    a_missing = [p for p in IMPACT_PLAYERS.get(away, []) if any(p in injured for injured in injury_data.get(away, []))]
    
    for _ in h_missing: h["off"] -= 7.0; h["def"] += 1.5
    for _ in a_missing: a["off"] -= 7.0; a["def"] += 1.5
    
    pace_f = ((h["pace"] + a["pace"]) / 2) / 100
    margin = (((h["off"]-h["def"]) - (a["off"]-a["def"])) / 2) * pace_f + 2.4
    return margin, pace_f, h_missing, a_missing

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    injuries = get_injury_report()
    
    try:
        r = requests.get(f"{BASE_URL}/odds/", params={"apiKey":ODDS_API_KEY, "regions":"us", "markets":"spreads", "oddsFormat":"decimal"})
        games = r.json()
    except: return

    # --- 5. 同場去重與最優選 ---
    best_picks = {} 

    for g in games:
        home, away = normalize_team(g["home_team"]), normalize_team(g["away_team"])
        game_id = f"{away}@{home}"
        
        for book in g.get("bookmakers", []):
            for market in book.get("markets", []):
                for o in market.get("outcomes"):
                    name = normalize_team(o["name"])
                    line = o.get("point", 0)
                    
                    margin, pf, h_m, a_m = predict_margin(home, away, injuries)
                    raw_target = margin if name == home else -margin
                    blended_target = (raw_target * MODEL_WEIGHT) + ((-line) * MARKET_WEIGHT)
                    
                    win_count = sum(1 for _ in range(SIMS) if (blended_target + random.gauss(0, DYNAMIC_STD_BASE) + line) > 0)
                    prob = win_count / SIMS
                    edge = prob - (1/o["price"])

                    if edge >= EDGE_THRESHOLD:
                        tier = "🔥強力" if edge > 0.05 else ("⭐穩定" if edge > 0.03 else "📊觀察")
                        inj_msg = f"⚠️ 缺陣: {', '.join([p.title() for p in (h_m + a_m)])}" if (h_m + a_m) else "✅ 陣容完整"
                        
                        pick_data = {
                            "edge": edge, "tier": tier, "prob": prob,
                            "msg": f"{tier} | {away}@{home}\n🎯 {TEAM_CN.get(name,name)} {line:+} @ **{o['price']}** ({book['title']})\n   └ {inj_msg} | 勝率: {prob:.1%} | 領先: {edge:+.1%}"
                        }
                        
                        if game_id not in best_picks or edge > best_picks[game_id]["edge"]:
                            best_picks[game_id] = pick_data

    # --- 6. 輸出 ---
    sorted_picks = sorted(best_picks.values(), key=lambda x: x["edge"], reverse=True)
    msg = f"🛡️ **NBA V84.2 Ultimate**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    
    if not sorted_picks: msg += "📭 市場盤口緊繃，無明顯偏差場次。"
    else:
        for p in sorted_picks[:6]: msg += f"\n{p['msg']}\n----------------"

    requests.post(WEBHOOK, json={"content": msg})

if __name__ == "__main__": run()
