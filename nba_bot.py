import requests
import os
import json
import random
from datetime import datetime, timedelta

# ==========================================
# NBA V91.0 - 2026 賽季分日期專業版
# ==========================================

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
RAPID_API_KEY = os.getenv("X_RAPIDAPI_KEY")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")

SIMS = 25000
EDGE_THRESHOLD = 0.025
MODEL_WEIGHT = 0.45
MARKET_WEIGHT = 0.55
DYNAMIC_STD_BASE = 14.8

# --- 1. 2026 最新球星數據庫 (依據最新交易校正) ---
IMPACT_PLAYERS = {
    "Los Angeles Lakers": ["doncic", "james", "davis"], # Luka 已轉至湖人
    "Golden State Warriors": ["curry", "butler", "green"], # Jimmy Butler 已轉至勇士
    "Dallas Mavericks": ["irving", "thompson", "ad"], # AD 已轉至獨行俠
    "New York Knicks": ["brunson", "towns", "bridges"],
    "Minnesota Timberwolves": ["edwards", "randle", "gobert"],
    "Oklahoma City Thunder": ["shai", "holmgren", "williams"],
    "Boston Celtics": ["tatum", "brown", "vucevic"], # Vucevic 已轉至塞爾提克
    "Milwaukee Bucks": ["giannis", "lillard"],
    "Phoenix Suns": ["durant", "booker", "beal"],
    "Sacramento Kings": ["fox", "sabonis", "derozan"],
    "Philadelphia 76ers": ["embiid", "maxey", "george"],
    "Denver Nuggets": ["jokic", "murray", "jones"], # Tyus Jones 已加盟金塊
    "Memphis Grizzlies": ["jackson", "bane"], # Morant 本季受傷風險高
    "San Antonio Spurs": ["wembanyama", "paul"],
    "Indiana Pacers": ["haliburton", "siakam"],
    "Cleveland Cavaliers": ["mitchell", "ball", "mobley"], # Lonzo Ball 在騎士
    "Miami Heat": ["adebayo", "herro", "powell"], # Norman Powell 在熱火
    "New Orleans Pelicans": ["williamson", "ingram", "murray"],
    "Houston Rockets": ["sengun", "green"],
    "Atlanta Hawks": ["young", "kuminga"], # Kuminga 已轉至老鷹
    "Chicago Bulls": ["lavine", "white", "okoro"], # Okoro 在公牛
    "Brooklyn Nets": ["thomas", "highsmith"],
    "Orlando Magic": ["banchero", "suggs"],
    "Toronto Raptors": ["barnes", "barrett"],
    "Utah Jazz": ["markkanen", "sexton"],
    "Portland Trail Blazers": ["simons", "grant"],
    "Detroit_Pistons": ["cunningham", "schroder"], # Schroder 在活塞
    "Charlotte Hornets": ["ball", "miller", "sexton"] # Sexton 有時出現在這
}

TEAM_CN = {"Boston Celtics":"塞爾提克","Milwaukee Bucks":"公鹿","Denver Nuggets":"金塊","Golden State Warriors":"勇士","Los Angeles Lakers":"湖人","Phoenix Suns":"太陽","Dallas Mavericks":"獨行俠","Los Angeles Clippers":"快艇","Miami Heat":"熱火","Philadelphia 76ers":"七六人","New York Knicks":"尼克","Toronto Raptors":"暴龍","Chicago Bulls":"公牛","Atlanta Hawks":"老鷹","Brooklyn Nets":"籃網","Cleveland Cavaliers":"騎士","Indiana Pacers":"溜馬","Detroit Pistons":"活塞","Orlando Magic":"魔術","Charlotte Hornets":"黃蜂","Washington Wizards":"巫師","Houston Rockets":"火箭","San Antonio Spurs":"馬刺","Memphis Grizzlies":"灰熊","New Orleans Pelicans":"鵜鶘","Minnesota Timberwolves":"灰狼","Oklahoma City Thunder":"雷霆","Utah Jazz":"爵士","Sacramento Kings":"國王","Portland Trail Blazers":"拓荒者"}

TEAM_STATS = {
    "Detroit Pistons": {"off":117,"def":110,"pace":100}, "Boston Celtics": {"off":114,"def":107,"pace":99},
    "New York Knicks": {"off":117,"def":111,"pace":96}, "Oklahoma City Thunder": {"off":113,"def":108,"pace":101},
    "Minnesota Timberwolves": {"off":117,"def":108,"pace":98}, "Denver Nuggets": {"off":118,"def":112,"pace":97},
    "Dallas Mavericks": {"off":113,"def":118,"pace":100}, "Sacramento Kings": {"off":111,"def":121,"pace":101}
    # ... 其餘隊伍延用 V89.0 ...
}

def normalize_team(name):
    if not name: return name
    n = name.lower()
    for full in TEAM_CN.keys():
        if n in full.lower() or full.lower() in n: return full
    return name

def get_injury_report():
    url = "https://sports-information.p.rapidapi.com/nba/injuries"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "sports-information.p.rapidapi.com"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        injured = {}
        for item in data:
            team = normalize_team(item.get("team", ""))
            player = item.get("player", "").lower()
            status = item.get("status", "").lower()
            if not any(s in status for s in ["available", "probable", "active"]):
                if team not in injured: injured[team] = []
                injured[team].append(player)
        return injured
    except: return {}

def predict_margin(home, away, injury_data):
    h = TEAM_STATS.get(home, {"off":114,"def":114,"pace":100}).copy()
    a = TEAM_STATS.get(away, {"off":114,"def":114,"pace":100}).copy()
    h_m = [k.capitalize() for k in IMPACT_PLAYERS.get(home, []) if any(k in p for p in injury_data.get(home, []))]
    a_m = [k.capitalize() for k in IMPACT_PLAYERS.get(away, []) if any(k in p for p in injury_data.get(away, []))]
    for _ in h_m: h["off"] -= 9.0; h["def"] += 2.0
    for _ in a_m: a["off"] -= 9.0; a["def"] += 2.0
    pace_f = ((h["pace"] + a["pace"]) / 2) / 100
    margin = (((h["off"]-h["def"]) - (a["off"]-a["def"])) / 2) * pace_f + 2.5
    return margin, h_m, a_m

def run():
    now_tw = datetime.utcnow() + timedelta(hours=8)
    today_s = now_tw.strftime('%Y-%m-%d')
    injuries = get_injury_report()
    
    try:
        r = requests.get("https://api.the-odds-api.com/v4/sports/basketball_nba/odds/", 
                         params={"apiKey":ODDS_API_KEY, "regions":"us", "markets":"spreads", "oddsFormat":"decimal"})
        games = r.json()
    except: return

    daily_picks = {}
    for g in games:
        c_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        g_date = c_time.strftime('%Y-%m-%d')
        home, away = normalize_team(g["home_team"]), normalize_team(g["away_team"])
        
        for book in g.get("bookmakers", []):
            for m in book.get("markets", []):
                for o in m.get("outcomes"):
                    name, line = normalize_team(o["name"]), o.get("point", 0)
                    if abs(line) > 22: continue 
                    margin, h_m, a_m = predict_margin(home, away, injuries)
                    target = margin if name == home else -margin
                    blended = (target * MODEL_WEIGHT) + ((-line) * MARKET_WEIGHT)
                    win_count = sum(1 for _ in range(SIMS) if (blended + random.gauss(0, DYNAMIC_STD_BASE) + line) > 0)
                    prob = win_count / SIMS
                    edge = prob - (1/o["price"])

                    if edge >= EDGE_THRESHOLD:
                        tier = "🔥強力" if edge > 0.05 else ("⭐穩定" if edge > 0.03 else "📊觀察")
                        msg = f"{tier} | {TEAM_CN.get(away,away)}@{TEAM_CN.get(home,home)} ({c_time.strftime('%H:%M')})\n🎯 {TEAM_CN.get(name,name)} {line:+} @ **{o['price']}** ({book['title']})\n   └ {'⚠️ 缺陣: '+', '.join(h_m+a_m) if h_m+a_m else '✅ 陣容完整'} | 勝率: {prob:.1%} | 領先: {edge:+.1%}"
                        if g_date not in daily_picks: daily_picks[g_date] = []
                        daily_picks[g_date].append({"edge": edge, "msg": msg})

    # --- 輸出分組 ---
    final_output = f"🛡️ **NBA V91.0 Ironclad**\n⏱ 更新: {now_tw.strftime('%m/%d %H:%M')}\n"
    for date in sorted(daily_picks.keys()):
        label = "📅 【今日賽事】" if date == today_s else f"⏭️ 【{date} 預告】"
        final_output += f"\n{label}\n"
        day_picks = sorted(daily_picks[date], key=lambda x: x["edge"], reverse=True)
        for p in day_picks[:5]: final_output += f"{p['msg']}\n"
        final_output += "----------------"

    requests.post(WEBHOOK, json={"content": final_output})

if __name__ == "__main__": run()
