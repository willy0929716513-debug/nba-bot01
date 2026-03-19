import requests
import os
import random
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("NBA_V97")

ODDS_API_KEY  = os.getenv("ODDS_API_KEY", "")
RAPID_API_KEY = os.getenv("X_RAPIDAPI_KEY", "")
WEBHOOK       = os.getenv("DISCORD_WEBHOOK", "")

SIMS             = 50000
EDGE_THRESHOLD   = 0.025
MODEL_WEIGHT     = 0.40
MARKET_WEIGHT    = 0.60
DYNAMIC_STD_BASE = 13.5
HOME_ADVANTAGE   = 2.8
MAX_SPREAD       = 18.0
MIN_SPREAD       = 1.0
DISCORD_CHAR_LIMIT = 1900

IMPACT_PLAYERS = {
    "Los Angeles Lakers":    ["doncic", "james", "reaves"],
    "Washington Wizards":    ["young", "davis", "sarr"],
    "Golden State Warriors": ["green", "kuminga", "hield"],
    "Cleveland Cavaliers":   ["harden", "mitchell", "allen"],
    "Los Angeles Clippers":  ["leonard", "zubac", "powell"],
    "Dallas Mavericks":      ["davis", "flagg", "irving"],
    "Boston Celtics":        ["tatum", "brown", "hauser"],
    "Denver Nuggets":        ["jokic", "murray", "gordon"],
    "Oklahoma City Thunder": ["shai", "holmgren", "mccain"],
    "San Antonio Spurs":     ["wembanyama", "harper", "cp3"],
    "Milwaukee Bucks":       ["giannis", "lillard", "dieng"],
    "New York Knicks":       ["brunson", "towns", "alvarado"],
    "Phoenix Suns":          ["durant", "booker", "beal"],
}

SEASON_OUT = {"irving", "haliburton", "butler", "curry", "vucevic", "tatum"}
SUPERSTARS = {"doncic", "jokic", "shai", "giannis", "durant", "james", "harden", "young"}
SUPERSTAR_PENALTY = 11.5
STAR_PENALTY = 8.0

FALLBACK_RATINGS = {
    "Los Angeles Lakers":    {"off": 118.5, "def": 112.0},
    "Boston Celtics":        {"off": 119.0, "def": 111.0},
    "Denver Nuggets":        {"off": 119.0, "def": 111.0},
    "Oklahoma City Thunder": {"off": 118.0, "def": 111.5},
    "Cleveland Cavaliers":   {"off": 117.5, "def": 112.5},
    "Golden State Warriors": {"off": 114.0, "def": 115.0},
    "Milwaukee Bucks":       {"off": 117.0, "def": 113.0},
    "New York Knicks":       {"off": 116.0, "def": 113.0},
    "Phoenix Suns":          {"off": 117.0, "def": 114.0},
    "San Antonio Spurs":     {"off": 115.0, "def": 116.0},
    "Dallas Mavericks":      {"off": 115.5, "def": 115.0},
    "Washington Wizards":    {"off": 113.0, "def": 117.0},
    "Los Angeles Clippers":  {"off": 115.0, "def": 114.5},
}
DEFAULT_RATING = {"off": 116.0, "def": 114.0}

TEAM_CN = {
    "Boston Celtics": "塞爾提克", "Milwaukee Bucks": "公鹿",
    "Denver Nuggets": "金塊", "Golden State Warriors": "勇士",
    "Los Angeles Lakers": "湖人", "Phoenix Suns": "太陽",
    "Dallas Mavericks": "獨行俠", "Los Angeles Clippers": "快艇",
    "Miami Heat": "熱火", "Philadelphia 76ers": "七六人",
    "New York Knicks": "尼克", "Toronto Raptors": "暴龍",
    "Chicago Bulls": "公牛", "Atlanta Hawks": "老鷹",
    "Brooklyn Nets": "籃網", "Cleveland Cavaliers": "騎士",
    "Indiana Pacers": "溜馬", "Detroit Pistons": "活塞",
    "Orlando Magic": "魔術", "Charlotte Hornets": "黃蜂",
    "Washington Wizards": "巫師", "Houston Rockets": "火箭",
    "San Antonio Spurs": "馬刺", "Memphis Grizzlies": "灰熊",
    "New Orleans Pelicans": "鵜鶘", "Minnesota Timberwolves": "灰狼",
    "Oklahoma City Thunder": "雷霆", "Utah Jazz": "爵士",
    "Sacramento Kings": "國王", "Portland Trail Blazers": "拓荒者",
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
            log.warning("Timeout attempt %d/%d: %s", attempt, retries, url)
        except requests.exceptions.HTTPError as e:
            log.error("HTTP error %s: %s", e.response.status_code, url)
            break
        except Exception as e:
            log.warning("Request failed attempt %d/%d: %s", attempt, retries, e)
    return None


def fetch_team_stats():
    headers = {
        "X-RapidAPI-Key":  RAPID_API_KEY,
        "X-RapidAPI-Host": "api-nba-v1.p.rapidapi.com",
    }
    data = safe_get(
        "https://api-nba-v1.p.rapidapi.com/standings",
        headers=headers,
        params={"league": "standard", "season": "2025"}
    )

    if not data or "response" not in data:
        log.warning("Team stats API failed, using fallback ratings")
        return {}

    ratings = {}
    for team_data in data["response"]:
        try:
            team_name = team_data["team"]["name"]
            full_name = normalize_team(team_name)
            if not full_name or full_name not in TEAM_CN:
                continue

            win   = int(team_data["win"]["total"])
            loss  = int(team_data["loss"]["total"])
            total = win + loss
            if total == 0:
                continue

            win_pct  = win / total
            last10_w = int(team_data["win"].get("lastTen", 5))
            form_score = (last10_w - 5) * 0.6

            off_rating = 110.0 + win_pct * 18.0
            def_rating = 120.0 - win_pct * 14.0

            ratings[full_name] = {
                "off":  round(off_rating, 1),
                "def":  round(def_rating, 1),
                "form": round(form_score, 2),
            }
        except (KeyError, TypeError, ValueError):
            continue

    log.info("Live team ratings loaded: %d teams", len(ratings))
    return ratings


def get_injury_report():
    url = "https://sports-information.p.rapidapi.com/nba/injuries"
    headers = {
        "X-RapidAPI-Key":  RAPID_API_KEY,
        "X-RapidAPI-Host": "sports-information.p.rapidapi.com",
    }
    data = safe_get(url, headers=headers)

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


def predict_margin(home, away, injury_data, live_ratings):
    h_base = live_ratings.get(home, FALLBACK_RATINGS.get(home, DEFAULT_RATING))
    a_base = live_ratings.get(away, FALLBACK_RATINGS.get(away, DEFAULT_RATING))
    h_stat = dict(h_base)
    a_stat = dict(a_base)

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

    h_form = h_base.get("form", 0.0)
    a_form = a_base.get("form", 0.0)

    h_net = (h_stat["off"] - h_stat["def"]) + h_form
    a_net = (a_stat["off"] - a_stat["def"]) + a_form

    margin = (h_net - a_net) / 2 + HOME_ADVANTAGE
    return margin, [p.capitalize() for p in h_missing], [p.capitalize() for p in a_missing]


def get_consensus_line(bookmakers, team_name):
    lines = []
    for book in bookmakers:
        for market in book.get("markets", []):
            if market.get("key") != "spreads":
                continue
            for outcome in market.get("outcomes", []):
                if normalize_team(outcome.get("name", "")) == team_name:
                    pt = outcome.get("point", None)
                    if pt is not None:
                        lines.append(pt)
    if not lines:
        return None
    return sum(lines) / len(lines)


def simulate_cover(blended, line):
    wins = sum(1 for _ in range(SIMS) if blended + random.gauss(0, DYNAMIC_STD_BASE) + line > 0)
    return wins / SIMS


def fetch_odds():
    params = {
        "apiKey":     ODDS_API_KEY,
        "regions":    "us",
        "markets":    "spreads",
        "oddsFormat": "decimal",
    }
    data = safe_get(
        "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/",
        params=params
    )
    if data is None:
        log.error("Odds API failed")
        return []
    log.info("Odds loaded: %d games", len(data))
    return data


def chunked_send(content, webhook):
    lines = content.split("\n")
    chunk, chunks = "", []
    for line in lines:
        if len(chunk) + len(line) + 1 > DISCORD_CHAR_LIMIT:
            chunks.append(chunk)
            chunk = line + "\n"
        else:
            chunk += line + "\n"
    if chunk:
        chunks.append(chunk)

    for i, part in enumerate(chunks, 1):
        label = "(%d/%d)\n%s" % (i, len(chunks), part) if len(chunks) > 1 else part
        try:
            r = requests.post(webhook, json={"content": label}, timeout=10)
            r.raise_for_status()
        except Exception as e:
            log.error("Discord send failed chunk %d: %s", i, e)


def run():
    if not all([ODDS_API_KEY, RAPID_API_KEY, WEBHOOK]):
        log.error("Missing env vars: ODDS_API_KEY / X_RAPIDAPI_KEY / DISCORD_WEBHOOK")
        return

    now_tw  = datetime.utcnow() + timedelta(hours=8)
    today_s = now_tw.strftime("%Y-%m-%d")

    live_ratings = fetch_team_stats()
    data_source  = "即時數據" if live_ratings else "靜態備用"
    log.info("Using: %s", data_source)

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
        margin, h_missing, a_missing = predict_margin(home, away, injuries, live_ratings)
        bookmakers = g.get("bookmakers", [])

        for book in bookmakers:
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

                    consensus = get_consensus_line(bookmakers, name)
                    if consensus is None:
                        consensus = line

                    target  = margin if name == home else -margin
                    blended = target * MODEL_WEIGHT + (-consensus) * MARKET_WEIGHT

                    prob = simulate_cover(blended, line)
                    edge = prob - (1 / price)

                    if edge < EDGE_THRESHOLD:
                        continue

                    missing = (h_missing if name == home else a_missing) + \
                              (a_missing if name == home else h_missing)

                    if edge > 0.07:
                        tier = "💎 頂級"
                    elif edge > 0.05:
                        tier = "🔥 強力"
                    else:
                        tier = "⭐ 穩定"

                    bet_cn        = TEAM_CN.get(name, name)
                    away_cn       = TEAM_CN.get(away, away)
                    home_cn       = TEAM_CN.get(home, home)
                    missing_str   = "缺陣: " + ", ".join(missing) if missing else "陣容完整"
                    consensus_str = "共識線: %+.1f" % consensus

                    msg = (
                        "**[%s] %s @ %s** (%s)\n"
                        "投注: `%s %+.1f` @ **%.2f** (%s)\n"
                        "> %s | %s | 勝率: %.1f%% | Edge: %+.1f%%\n"
                    ) % (
                        tier, away_cn, home_cn,
                        c_time.strftime("%m/%d %H:%M"),
                        bet_cn, line, price, book.get("title", "?"),
                        missing_str, consensus_str, prob * 100, edge * 100
                    )

                    existing = daily_picks[g_date].get(game_id)
                    if existing is None or edge > existing["edge"]:
                        daily_picks[g_date][game_id] = {"edge": edge, "msg": msg}

    total_picks = sum(len(v) for v in daily_picks.values())
    avg_edge = (
        sum(p["edge"] for d in daily_picks.values() for p in d.values()) / total_picks
        if total_picks else 0
    )

    output = "🏀 NBA V97.0 | 更新: %s | 資料: %s | 推薦: %d 場 | 平均Edge: %+.1f%%\n" % (
        now_tw.strftime("%m/%d %H:%M"), data_source, total_picks, avg_edge * 100
    )

    if not daily_picks:
        output += "\n今日無符合條件之推薦。\n"
    else:
        for date in sorted(daily_picks):
            label = "📅 今日賽事" if date == today_s else ("⏭ 預告 %s" % date)
            output += "\n%s\n" % label
            for p in sorted(daily_picks[date].values(), key=lambda x: x["edge"], reverse=True):
                output += p["msg"]
            output += "-" * 30 + "\n"

    log.info("Sending to Discord, length: %d", len(output))
    chunked_send(output, WEBHOOK)
    log.info("Done")


if __name__ == "__main__":
    run()
