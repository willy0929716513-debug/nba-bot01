import requests
import os
import json
import random
from datetime import datetime, timedelta

# ==========================================
# NBA V83.0 Final - Market Scanner + Auto Injury
# ==========================================

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
RAPID_API_KEY = os.getenv("X_RAPIDAPI_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba"

# --- 核心分析參數 ---
SIMS = 20000
EDGE_THRESHOLD = 0.01      # 1% 以上就顯示，讓你觀察全盤
MODEL_WEIGHT = 0.45        # 模型佔比
MARKET_WEIGHT = 0.55       # 市場權重 (避險)

# 明星球員名單 (對戰力影響最大的人)
IMPACT_PLAYERS = {
    "Boston Celtics": ["Jayson Tatum", "Jaylen Brown", "Kristaps Porzingis"],
    "Denver Nuggets": ["Nikola Jokic", "Jamal Murray"],
    "Milwaukee Bucks": ["Giannis Antetokounmpo", "Damian Lillard"],
    "Dallas Mavericks": ["Luka Doncic", "Kyrie Irving"],
    "Philadelphia 76ers": ["Joel Embiid", "Tyrese Maxey"],
    "Los Angeles Lakers": ["LeBron James", "Anthony Davis"],
    "Golden State Warriors": ["Stephen Curry"],
    "Phoenix Suns": ["Kevin Durant", "Devin Booker"],
    "Minnesota Timberwolves": ["Anthony Edwards", "Karl-Anthony Towns"],
    "Oklahoma City Thunder": ["Shai Gilgeous-Alexander", "Chet Holmgren"],
    "Sacramento Kings": ["De'Aaron Fox", "Domantas Sabonis"],
    "Indiana Pacers": ["Tyrese Haliburton"],
    "Miami Heat": ["Jimmy Butler", "Bam Adebayo"],
    "New York Knicks": ["Jalen Brunson"]
}

TEAM_CN = {"Boston Celtics":"塞爾提克","Milwaukee Bucks":"公鹿","Denver Nuggets":"金塊","Golden State Warriors":"勇士","Los Angeles Lakers":"湖人","Phoenix Suns":"太陽","Dallas Mavericks":"獨行俠","Los Angeles Clippers":"快艇","Miami Heat":"熱火","Philadelphia 76ers":"七六人","New York Knicks":"尼克","Toronto Raptors":"暴龍","Chicago Bulls":"公牛","Atlanta Hawks":"老鷹","Brooklyn Nets":"籃網","Cleveland Cavaliers":"騎士","Indiana Pacers":"溜馬","Detroit Pistons":"活塞","Orlando Magic":"魔術","Charlotte Hornets":"黃蜂","Washington Wizards":"巫師","Houston Rockets":"火箭","San Antonio Spurs":"馬刺","Memphis Grizzlies":"灰熊","New Orleans Pelicans":"鵜鶘","Minnesota Timberwolves":"灰狼","Oklahoma City Thunder":"雷霆","Utah Jazz":"爵士","Sacramento Kings":"國王","Portland Trail Blazers":"拓荒者"}

# 球隊基礎數據 (2026 最新版)
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

# --- 1. 自動抓取傷病報告 ---
def get_injury_report():
    url = "https://nba-injury-reports.p.rapidapi.com/"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "nba-injury-reports.p.rapidapi.com"}
    injured_dict = {}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        for item in data:
            status = item.get("status", "").lower()
            if any(s in status for s in ["out", "questionable", "doubtful"]):
                team, player = item.get("team"), item.get("player")
                if team not in injured_dict: injured_dict[team] = []
                injured_dict[team].append(player)
        return injured_dict
    except: return {}

# --- 2. 核心分析：加入傷病懲罰 ---
def predict_margin(home, away, injury_data):
    h = TEAM_STATS.get(home, {"off":114,"def":114,"pace":100}).copy()
    a = TEAM_STATS.get(away, {"off":114,"def":114,"pace":100}).copy()
    
    # 模糊匹配名稱 (例如 "LeBron James" vs "James, LeBron")
    h_missing = [p for p in IMPACT_PLAYERS.get(home, []) if any(p in name for name in injury_data.get(home, []))]
    a_missing = [p for p in IMPACT_PLAYERS.get(away, []) if any(p in name for name in injury_data.get(away, []))]
    
    # 權重懲罰：主將缺陣對 OffRtg 與 DefRtg 的影響
    for _ in h_missing: h["off"] -= 6.0; h["def"] += 2.0
    for _ in a_missing: a["off"] -= 6.0; a["def"] += 2.0
    
    pace_factor = ((h["pace"] + a["pace"]) / 2) / 100
    base_margin = (((h["off"]-h["def"]) - (a["off"]-a["def"])) / 2) * pace_factor
    return base_margin + 2.4, pace_factor, h_missing, a_missing

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    injuries = get_injury_report() # 先抓傷病
    
    try:
        r = requests.get(f"{BASE_URL}/odds/", params={"apiKey":ODDS_API_KEY, "regions":"us", "markets":"spreads", "oddsFormat":"decimal"})
        games = r.json()
    except: return

    report = []
    for g in games:
        home, away = g["home_team"], g["away_team"]
        raw_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        if abs((raw_time - now_tw).total_seconds()) > 86400: continue

        # 多莊家比價
        best_outcomes = {}
        for book in g.get("bookmakers", []):
            for market in book.get("markets", []):
                for o in market.get("outcomes"):
                    key = (o["name"], o["point"])
                    if key not in best_outcomes or o["price"] > best_outcomes[key]["price"]:
                        best_outcomes[key] = {"price": o["price"], "source": book["title"]}

        for (name, line), data in best_outcomes.items():
            model_margin, pace_f, h_abs, a_abs = predict_margin(home, away, injuries)
            raw_target = model_margin if name == home else -model_margin
            blended_target = (raw_target * MODEL_WEIGHT) + ((-line) * MARKET_WEIGHT)
            
            win_count = sum(1 for _ in range(SIMS) if (blended_target + random.gauss(0, 12.5 + pace_f*1.5) + line) > 0)
            prob = win_count / SIMS
            edge = prob - (1/data["price"])

            if edge >= EDGE_THRESHOLD:
                tier = "🔥強力" if edge > 0.04 else ("⭐穩定" if edge > 0.02 else "📊觀察")
                inj_alert = f"⚠️ 缺陣: {', '.join(h_abs + a_abs)}" if (h_abs + a_abs) else "✅ 陣容完整"
                
                report.append({
                    "edge": edge,
                    "msg": f"{tier} | {TEAM_CN.get(away,away)}@{TEAM_CN.get(home,home)}\n🎯 {TEAM_CN.get(name,name)} {line:+} @ **{data['price']}** ({data['source']})\n   └ {inj_alert} | 勝率: {prob:.1%} | 領先: {edge:+.1%}"
                })

    report.sort(key=lambda x: x["edge"], reverse=True)
    msg = f"🛡️ **NBA V83.0 Active Roster**\n⏱ {now_tw.strftime('%m/%d %H:%M')}\n"
    if not report: msg += "📭 市場穩定，無明顯獲利空間。"
    else:
        for r in report[:8]: msg += f"\n{r['msg']}\n----------------"

    requests.post(WEBHOOK, json={"content": msg})

if __name__ == "__main__": run()
