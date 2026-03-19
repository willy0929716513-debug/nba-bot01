import requests
import os
import random
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(“NBA_V96”)

ODDS_API_KEY  = os.getenv(“ODDS_API_KEY”, “”)
RAPID_API_KEY = os.getenv(“X_RAPIDAPI_KEY”, “”)
WEBHOOK       = os.getenv(“DISCORD_WEBHOOK”, “”)

SIMS             = 30000
EDGE_THRESHOLD   = 0.025
MODEL_WEIGHT     = 0.45
MARKET_WEIGHT    = 0.55
DYNAMIC_STD_BASE = 14.8
HOME_ADVANTAGE   = 2.8
MAX_SPREAD       = 22.0
MIN_SPREAD       = 0.5
DISCORD_CHAR_LIMIT = 1900

IMPACT_PLAYERS = {
“Los Angeles Lakers”:    [“doncic”, “james”, “ayton”],
“Washington Wizards”:    [“young”, “davis”, “sarr”],
“Golden State Warriors”: [“curry”, “porzingis”, “green”],
“Cleveland Cavaliers”:   [“harden”, “mitchell”, “allen”],
“Los Angeles Clippers”:  [“garland”, “leonard”, “powell”],
“Dallas Mavericks”:      [“flagg”, “thompson”, “irving”],
“Boston Celtics”:        [“tatum”, “brown”, “vucevic”],
“Denver Nuggets”:        [“jokic”, “murray”, “tyus”],
“Oklahoma City Thunder”: [“shai”, “holmgren”, “williams”],
“San Antonio Spurs”:     [“wembanyama”, “harper”, “cp3”],
“Milwaukee Bucks”:       [“giannis”, “lillard”, “dieng”],
“New York Knicks”:       [“brunson”, “towns”, “alvarado”],
“Phoenix Suns”:          [“durant”, “booker”, “beal”],
}

SEASON_OUT = {“irving”, “haliburton”, “butler”}
SUPERSTARS = {“doncic”, “jokic”, “shai”, “giannis”, “curry”, “durant”, “james”}
SUPERSTAR_PENALTY = 11.5
STAR_PENALTY = 8.0

TEAM_RATINGS = {
“Los Angeles Lakers”:    {“off”: 118.5, “def”: 112.0},
“Boston Celtics”:        {“off”: 120.0, “def”: 110.5},
“Denver Nuggets”:        {“off”: 119.0, “def”: 111.0},
“Oklahoma City Thunder”: {“off”: 118.0, “def”: 111.5},
“Cleveland Cavaliers”:   {“off”: 117.5, “def”: 112.5},
“Golden State Warriors”: {“off”: 116.5, “def”: 113.5},
“Milwaukee Bucks”:       {“off”: 117.0, “def”: 113.0},
“New York Knicks”:       {“off”: 116.0, “def”: 113.0},
“Phoenix Suns”:          {“off”: 117.0, “def”: 114.0},
“San Antonio Spurs”:     {“off”: 115.0, “def”: 116.0},
“Dallas Mavericks”:      {“off”: 115.5, “def”: 115.0},
“Washington Wizards”:    {“off”: 116.0, “def”: 115.5},
“Los Angeles Clippers”:  {“off”: 115.0, “def”: 114.5},
}
DEFAULT_RATING = {“off”: 116.0, “def”: 114.0}

TEAM_CN = {
“Boston Celtics”: “celtics”, “Milwaukee Bucks”: “bucks”,
“Denver Nuggets”: “nuggets”, “Golden State Warriors”: “warriors”,
“Los Angeles Lakers”: “lakers”, “Phoenix Suns”: “suns”,
“Dallas Mavericks”: “mavs”, “Los Angeles Clippers”: “clippers”,
“Miami Heat”: “heat”, “Philadelphia 76ers”: “sixers”,
“New York Knicks”: “knicks”, “Toronto Raptors”: “raptors”,
“Chicago Bulls”: “bulls”, “Atlanta Hawks”: “hawks”,
“Brooklyn Nets”: “nets”, “Cleveland Cavaliers”: “cavs”,
“Indiana Pacers”: “pacers”, “Detroit Pistons”: “pistons”,
“Orlando Magic”: “magic”, “Charlotte Hornets”: “hornets”,
“Washington Wizards”: “wizards”, “Houston Rockets”: “rockets”,
“San Antonio Spurs”: “spurs”, “Memphis Grizzlies”: “grizzlies”,
“New Orleans Pelicans”: “pelicans”, “Minnesota Timberwolves”: “wolves”,
“Oklahoma City Thunder”: “thunder”, “Utah Jazz”: “jazz”,
“Sacramento Kings”: “kings”, “Portland Trail Blazers”: “blazers”,
}

def normalize_team(name):
if not name:
return name
n = name.lower()
for full in TEAM_CN:
if n in full.lower() or full.lower() in n:
return full
return name

def safe_get(url, headers=None, params=None, retries=3, timeout=15):
for attempt in range(1, retries + 1):
try:
r = requests.get(url, headers=headers, params=params, timeout=timeout)
r.raise_for_status()
return r.json()
except requests.exceptions.Timeout:
log.warning(“Timeout attempt %d/%d: %s”, attempt, retries, url)
except requests.exceptions.HTTPError as e:
log.error(“HTTP error %s: %s”, e.response.status_code, url)
break
except Exception as e:
log.warning(“Request failed attempt %d/%d: %s”, attempt, retries, e)
return None

def get_injury_report():
url = “https://sports-information.p.rapidapi.com/nba/injuries”
headers = {
“X-RapidAPI-Key”:  RAPID_API_KEY,
“X-RapidAPI-Host”: “sports-information.p.rapidapi.com”,
}
data = safe_get(url, headers=headers)

```
if not data:
    log.warning("Injury API failed, using SEASON_OUT fallback")
    fallback = {}
    for team, players in IMPACT_PLAYERS.items():
        out = [p for p in players if p in SEASON_OUT]
        if out:
            fallback[team] = out
    return fallback

injured = {}
skip_statuses = {"available", "probable", "active"}
for item in data:
    team   = normalize_team(item.get("team", ""))
    player = item.get("player", "").lower()
    status = item.get("status", "").lower()
    if not any(s in status for s in skip_statuses):
        injured.setdefault(team, []).append(player)

for team, players in IMPACT_PLAYERS.items():
    for p in players:
        if p in SEASON_OUT and p not in injured.get(team, []):
            injured.setdefault(team, []).append(p)

log.info("Injury report loaded: %d entries", sum(len(v) for v in injured.values()))
return injured
```

def predict_margin(home, away, injury_data):
h_stat = dict(TEAM_RATINGS.get(home, DEFAULT_RATING))
a_stat = dict(TEAM_RATINGS.get(away, DEFAULT_RATING))

```
def get_missing(team):
    injured_lower = [p.lower() for p in injury_data.get(team, [])]
    return [
        k for k in IMPACT_PLAYERS.get(team, [])
        if k in SEASON_OUT or any(k in p for p in injured_lower)
    ]

h_missing = get_missing(home)
a_missing = get_missing(away)

for p in h_missing:
    penalty = SUPERSTAR_PENALTY if p in SUPERSTARS else STAR_PENALTY
    h_stat["off"] -= penalty * 0.6
    h_stat["def"] += penalty * 0.4

for p in a_missing:
    penalty = SUPERSTAR_PENALTY if p in SUPERSTARS else STAR_PENALTY
    a_stat["off"] -= penalty * 0.6
    a_stat["def"] += penalty * 0.4

margin = ((h_stat["off"] - h_stat["def"]) - (a_stat["off"] - a_stat["def"])) / 2 + HOME_ADVANTAGE
return margin, [p.capitalize() for p in h_missing], [p.capitalize() for p in a_missing]
```

def simulate_cover(blended, line):
wins = sum(1 for _ in range(SIMS) if blended + random.gauss(0, DYNAMIC_STD_BASE) + line > 0)
return wins / SIMS

def fetch_odds():
params = {
“apiKey”:     ODDS_API_KEY,
“regions”:    “us”,
“markets”:    “spreads”,
“oddsFormat”: “decimal”,
}
data = safe_get(“https://api.the-odds-api.com/v4/sports/basketball_nba/odds/”, params=params)
if data is None:
log.error(“Odds API failed”)
return []
log.info(“Odds loaded: %d games”, len(data))
return data

def chunked_send(content, webhook):
lines = content.split(”\n”)
chunk, chunks = “”, []
for line in lines:
if len(chunk) + len(line) + 1 > DISCORD_CHAR_LIMIT:
chunks.append(chunk)
chunk = line + “\n”
else:
chunk += line + “\n”
if chunk:
chunks.append(chunk)

```
for i, part in enumerate(chunks, 1):
    label = "(%d/%d)\n%s" % (i, len(chunks), part) if len(chunks) > 1 else part
    try:
        r = requests.post(webhook, json={"content": label}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.error("Discord send failed chunk %d: %s", i, e)
```

def run():
if not all([ODDS_API_KEY, RAPID_API_KEY, WEBHOOK]):
log.error(“Missing env vars: ODDS_API_KEY / X_RAPIDAPI_KEY / DISCORD_WEBHOOK”)
return

```
now_tw  = datetime.utcnow() + timedelta(hours=8)
today_s = now_tw.strftime("%Y-%m-%d")

injuries = get_injury_report()
games    = fetch_odds()
if not games:
    return

daily_picks = {}

for g in games:
    try:
        c_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
    except (KeyError, ValueError):
        continue

    g_date  = c_time.strftime("%Y-%m-%d")
    home    = normalize_team(g.get("home_team", ""))
    away    = normalize_team(g.get("away_team", ""))
    game_id = "%s@%s" % (away, home)

    daily_picks.setdefault(g_date, {})
    margin, h_missing, a_missing = predict_margin(home, away, injuries)

    for book in g.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != "spreads":
                continue
            for outcome in market.get("outcomes", []):
                name  = normalize_team(outcome.get("name", ""))
                line  = outcome.get("point", 0)
                price = outcome.get("price", 0)

                if not (MIN_SPREAD <= abs(line) <= MAX_SPREAD):
                    continue
                if price <= 1.0:
                    continue

                target  = margin if name == home else -margin
                blended = target * MODEL_WEIGHT + (-line) * MARKET_WEIGHT
                prob    = simulate_cover(blended, line)
                edge    = prob - (1 / price)

                if edge < EDGE_THRESHOLD:
                    continue

                missing = (h_missing if name == home else a_missing) + \
                          (a_missing if name == home else h_missing)

                tier = "TOP" if edge > 0.07 else ("STRONG" if edge > 0.05 else "SOLID")
                bet_cn  = TEAM_CN.get(name, name)
                away_cn = TEAM_CN.get(away, away)
                home_cn = TEAM_CN.get(home, home)
                missing_str = "OUT: " + ", ".join(missing) if missing else "FULL ROSTER"

                msg = (
                    "**[%s] %s @ %s** (%s)\n"
                    "Bet: `%s %+.1f` @ **%.2f** (%s)\n"
                    "> %s | Win%%: %.1f%% | Edge: %+.1f%%\n"
                ) % (
                    tier, away_cn, home_cn,
                    c_time.strftime("%m/%d %H:%M"),
                    bet_cn, line, price, book.get("title", "?"),
                    missing_str, prob * 100, edge * 100
                )

                existing = daily_picks[g_date].get(game_id)
                if existing is None or edge > existing["edge"]:
                    daily_picks[g_date][game_id] = {"edge": edge, "msg": msg}

total_picks = sum(len(v) for v in daily_picks.values())
avg_edge = (
    sum(p["edge"] for d in daily_picks.values() for p in d.values()) / total_picks
    if total_picks else 0
)

output = "NBA V96.0 | Updated: %s | Picks: %d | Avg Edge: %+.1f%%\n" % (
    now_tw.strftime("%m/%d %H:%M"), total_picks, avg_edge * 100
)

if not daily_picks:
    output += "\nNo qualifying picks today.\n"
else:
    for date in sorted(daily_picks):
        label = "[TODAY]" if date == today_s else ("[%s]" % date)
        output += "\n%s\n" % label
        for p in sorted(daily_picks[date].values(), key=lambda x: x["edge"], reverse=True):
            output += p["msg"]
        output += "-" * 30 + "\n"

log.info("Sending to Discord, length: %d", len(output))
chunked_send(output, WEBHOOK)
log.info("Done")
```

if **name** == “**main**”:
run()