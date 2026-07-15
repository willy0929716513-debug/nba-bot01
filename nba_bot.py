import requests
import os
import random
import logging
import json
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("NBA_V2")

VERSION = "V2.0"

ODDS_API_KEY    = os.getenv("ODDS_API_KEY", "")
WEBHOOK         = os.getenv("DISCORD_WEBHOOK", "")
GITHUB_TOKEN    = os.getenv("GH_TOKEN", "")
BALLDONTLIE_KEY = os.getenv("BALLDONTLIE_KEY", "")

SITE_DATA_PATH = os.getenv("SITE_DATA_PATH", "docs/data/latest.json")


def current_season_year(now=None):
    """NBA season is labeled by its starting year (e.g. 2025-26 season -> 2025).
    Season kicks off in October, so before October the *previous* calendar
    year is still the season currently in progress / most recently completed.
    """
    now = now or datetime.utcnow()
    return now.year if now.month >= 10 else now.year - 1


SEASON_YEAR = current_season_year()

# Neither the-odds-api nor ESPN document a stable Summer League identifier,
# and the host city (hence the ESPN slug) shifts year to year, so both are
# discovered/tried defensively at runtime instead of hardcoded to one value.
SUMMER_LEAGUE_ESPN_SLUGS = [
    "nba-summer-las-vegas",
    "nba-summer-league",
    "nba-summer-utah",
    "nba-summer-sacramento",
    "nba-summer-california",
]

SIMS             = 50000
EDGE_THRESHOLD   = 0.06
MODEL_WEIGHT     = 0.35
MARKET_WEIGHT    = 0.65
DYNAMIC_STD_BASE = 13.0
HOME_ADVANTAGE   = 2.8
MAX_SPREAD       = 15.0
MIN_SPREAD       = 3.0
MIN_PRICE        = 1.75
MAX_PRICE        = 2.15
DISCORD_CHAR_LIMIT = 1900
BANKROLL         = 1000.0
KELLY_FRACTION   = 0.20

IMPACT_PLAYERS = {
    "Los Angeles Lakers":     ["doncic", "james", "reaves"],
    "Washington Wizards":     ["mccollum", "davis", "sarr"],
    "Golden State Warriors":  ["podziemski", "porzingis", "green"],
    "Cleveland Cavaliers":    ["harden", "mitchell", "mobley"],
    "Los Angeles Clippers":   ["leonard", "garland", "brown"],
    "Dallas Mavericks":       ["flagg", "thompson", "jones"],
    "Boston Celtics":         ["brown", "white", "vucevic"],
    "Denver Nuggets":         ["jokic", "murray", "gordon"],
    "Oklahoma City Thunder":  ["shai", "holmgren", "mccain"],
    "San Antonio Spurs":      ["wembanyama", "fox", "cp3"],
    "Milwaukee Bucks":        ["giannis", "turner", "dieng"],
    "New York Knicks":        ["brunson", "towns", "alvarado"],
    "Houston Rockets":        ["durant", "sengun", "sheppard"],
    "Indiana Pacers":         ["siakam", "zubac", "nembhard"],
    "Philadelphia 76ers":     ["maxey", "embiid", "oubre"],
    "Minnesota Timberwolves": ["randle", "edwards", "gobert"],
    "Miami Heat":             ["adebayo", "herro", "collins"],
    "Portland Trail Blazers": ["avdija", "clingan", "grant"],
    "Detroit Pistons":        ["cunningham", "duren", "jenkins"],
    "Sacramento Kings":       ["fox", "monk", "keegan"],
    "Atlanta Hawks":          ["young", "hunter", "okongwu"],
    "Chicago Bulls":          ["white", "drummond", "dosunmu"],
    "Charlotte Hornets":      ["lamelo", "bridges", "miles"],
    "Orlando Magic":          ["banchero", "suggs", "wagner"],
    "Toronto Raptors":        ["ingram", "quickley", "boucher"],
    "Memphis Grizzlies":      ["morant", "jackson", "aldama"],
    "New Orleans Pelicans":   ["zion", "murphy", "daniels"],
    "Utah Jazz":              ["markkanen", "george", "kessler"],
    "Brooklyn Nets":          ["porter", "claxton", "thomas"],
    "Phoenix Suns":           ["booker", "green", "brooks"],
}

# NOTE: these two sets are a *manual fallback* only, merged in when the live
# RotoWire scrape (get_injury_report) can't confirm a status on its own. They
# go stale every offseason/trade-deadline and must be re-verified against
# current injury reports before each new season — last reviewed 2026-07,
# intentionally left empty at the start of a new season since no prior
# season's season-ending injury still applies and rosters shift heavily in
# the offseason. Populate as real season-long injuries are confirmed.
SEASON_OUT = set()

LIMITED_PLAYERS = set()

SUPERSTARS = {
    "doncic", "jokic", "shai", "giannis", "durant",
    "james", "harden", "embiid", "randle", "edwards",
    "wembanyama", "morant", "banchero", "young", "fox",
}

SUPERSTAR_PENALTY = 11.5
STAR_PENALTY      = 8.0
LIMITED_PENALTY   = 5.0

FALLBACK_RATINGS = {
    "Los Angeles Lakers":     {"off": 120.0, "def": 111.0},
    "Boston Celtics":         {"off": 117.5, "def": 111.5},
    "Denver Nuggets":         {"off": 119.0, "def": 111.0},
    "Oklahoma City Thunder":  {"off": 120.0, "def": 109.5},
    "Cleveland Cavaliers":    {"off": 118.0, "def": 111.5},
    "Golden State Warriors":  {"off": 114.0, "def": 115.0},
    "Milwaukee Bucks":        {"off": 116.5, "def": 113.0},
    "New York Knicks":        {"off": 116.5, "def": 112.5},
    "Houston Rockets":        {"off": 118.5, "def": 112.0},
    "San Antonio Spurs":      {"off": 115.5, "def": 115.5},
    "Dallas Mavericks":       {"off": 113.0, "def": 116.0},
    "Washington Wizards":     {"off": 113.0, "def": 117.0},
    "Los Angeles Clippers":   {"off": 116.0, "def": 113.5},
    "Indiana Pacers":         {"off": 115.5, "def": 114.0},
    "Phoenix Suns":           {"off": 115.5, "def": 115.0},
    "Philadelphia 76ers":     {"off": 115.0, "def": 114.5},
    "Minnesota Timberwolves": {"off": 116.5, "def": 112.5},
    "Miami Heat":             {"off": 114.5, "def": 114.0},
    "Portland Trail Blazers": {"off": 112.0, "def": 117.0},
    "Detroit Pistons":        {"off": 119.5, "def": 110.0},
    "Sacramento Kings":       {"off": 115.5, "def": 114.5},
    "Atlanta Hawks":          {"off": 115.5, "def": 115.0},
    "Chicago Bulls":          {"off": 113.5, "def": 116.0},
    "Charlotte Hornets":      {"off": 113.0, "def": 116.5},
    "Orlando Magic":          {"off": 114.0, "def": 113.5},
    "Toronto Raptors":        {"off": 113.0, "def": 116.0},
    "Memphis Grizzlies":      {"off": 116.5, "def": 113.5},
    "New Orleans Pelicans":   {"off": 113.5, "def": 115.5},
    "Utah Jazz":              {"off": 112.5, "def": 117.0},
    "Brooklyn Nets":          {"off": 114.0, "def": 116.0},
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


def _player_marked_out(text, player, team_nickname, out_keywords, skip_keywords):
    """Scan every occurrence of `player` in `text`, not just the first.

    Many players share a surname across the league (Davis, Brown, Williams,
    Green, Jones, ...), and IMPACT_PLAYERS only stores last names, so a
    single `text.find()` can lock onto an unrelated mention (a different
    player, a nav link, an unrelated story) and never look further. Each
    occurrence is only trusted if the team's nickname also appears nearby,
    which is how RotoWire groups players under a team heading.
    """
    start = 0
    while True:
        idx = text.find(player, start)
        if idx == -1:
            return False
        start = idx + len(player)
        wide_window = text[max(0, idx - 300):idx + 300]
        if team_nickname not in wide_window:
            continue
        local_window = text[max(0, idx - 80):idx + 200]
        if any(s in local_window for s in skip_keywords):
            continue
        if any(s in local_window for s in out_keywords):
            return True


def get_injury_report():
    try:
        url  = "https://www.rotowire.com/basketball/injury-report.php"
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        r = requests.get(url, headers=hdrs, timeout=15)
        r.raise_for_status()

        injured = {}
        text = r.text.lower()

        out_keywords  = ["ruled out", "will not play", "is out", "has been ruled out", "out ("]
        skip_keywords = ["questionable", "probable", "available", "good to go", "day-to-day"]

        for full_team in IMPACT_PLAYERS:
            nickname = full_team.split()[-1].lower()
            for player in IMPACT_PLAYERS[full_team]:
                if player not in text:
                    continue
                if _player_marked_out(text, player, nickname, out_keywords, skip_keywords):
                    if player not in injured.get(full_team, []):
                        injured.setdefault(full_team, []).append(player)

        for team, players in IMPACT_PLAYERS.items():
            for p in players:
                if p in SEASON_OUT and p not in injured.get(team, []):
                    injured.setdefault(team, []).append(p)

        log.info("RotoWire injury loaded: %d entries", sum(len(v) for v in injured.values()))
        return injured

    except Exception as e:
        log.warning("RotoWire failed: %s, using SEASON_OUT fallback", e)
        fallback = {}
        for team, players in IMPACT_PLAYERS.items():
            out = [p for p in players if p in SEASON_OUT]
            if out:
                fallback[team] = out
        return fallback


def fetch_team_stats():
    if not BALLDONTLIE_KEY:
        return {}
    headers = {"Authorization": BALLDONTLIE_KEY}
    data = safe_get(
        "https://api.balldontlie.io/v1/games",
        headers=headers,
        params={"seasons[]": SEASON_YEAR, "per_page": 100},
    )
    if not data or "data" not in data:
        return {}

    win_loss = {}
    for game in data["data"]:
        if game.get("status") != "Final":
            continue
        home = normalize_team(game["home_team"]["full_name"])
        away = normalize_team(game["visitor_team"]["full_name"])
        hs   = game.get("home_team_score", 0)
        vs   = game.get("visitor_team_score", 0)
        if hs and vs:
            win_loss.setdefault(home, {"w": 0, "l": 0})
            win_loss.setdefault(away, {"w": 0, "l": 0})
            if hs > vs:
                win_loss[home]["w"] += 1
                win_loss[away]["l"] += 1
            else:
                win_loss[away]["w"] += 1
                win_loss[home]["l"] += 1

    ratings = {}
    for team, rec in win_loss.items():
        if team not in TEAM_CN:
            continue
        total   = rec["w"] + rec["l"]
        win_pct = rec["w"] / total if total else 0.5
        ratings[team] = {
            "off":  round(110.0 + win_pct * 18.0, 1),
            "def":  round(120.0 - win_pct * 14.0, 1),
            "form": round((win_pct - 0.5) * 4, 2),
        }

    log.info("Game-based ratings loaded: %d teams", len(ratings))
    return ratings


def load_history():
    if not GITHUB_TOKEN:
        return {}
    headers = {"Authorization": "token %s" % GITHUB_TOKEN}
    gists   = safe_get("https://api.github.com/gists", headers=headers)
    if not gists:
        return {}
    for g in gists:
        if g.get("description") == "nba_bot_history":
            raw_url = list(g["files"].values())[0]["raw_url"]
            data    = safe_get(raw_url)
            return data if isinstance(data, dict) else {}
    return {}


def save_history(history):
    if not GITHUB_TOKEN:
        return
    headers = {
        "Authorization": "token %s" % GITHUB_TOKEN,
        "Content-Type":  "application/json",
    }
    content = json.dumps(history, ensure_ascii=False, indent=2)
    gists   = safe_get("https://api.github.com/gists", headers=headers)
    gist_id = None
    if gists:
        for g in gists:
            if g.get("description") == "nba_bot_history":
                gist_id = g["id"]
                break
    payload = {
        "description": "nba_bot_history",
        "public":      False,
        "files":       {"history.json": {"content": content}},
    }
    try:
        if gist_id:
            requests.patch(
                "https://api.github.com/gists/%s" % gist_id,
                headers=headers, json=payload, timeout=10,
            )
        else:
            requests.post(
                "https://api.github.com/gists",
                headers=headers, json=payload, timeout=10,
            )
        log.info("History saved to Gist")
    except Exception as e:
        log.error("Failed to save history: %s", e)


def calc_performance(history):
    total = win = 0
    profit = 0.0
    for record in history.values():
        if record.get("result") not in ["win", "loss"]:
            continue
        total += 1
        stake = record.get("kelly_stake", 10.0)
        if record["result"] == "win":
            win    += 1
            profit += stake * (record.get("price", 1.9) - 1)
        else:
            profit -= stake
    win_rate = (win / total * 100) if total else 0
    return total, win, win_rate, profit


def kelly_stake(prob, price, bankroll, fraction=KELLY_FRACTION):
    b = price - 1
    if b <= 0:
        return 0.0
    q = 1 - prob
    k = (b * prob - q) / b
    k = max(0.0, k) * fraction
    return round(bankroll * k, 1)


def predict_margin(home, away, injury_data, live_ratings):
    h_base = live_ratings.get(home, FALLBACK_RATINGS.get(home, DEFAULT_RATING))
    a_base = live_ratings.get(away, FALLBACK_RATINGS.get(away, DEFAULT_RATING))
    h_stat = dict(h_base)
    a_stat = dict(a_base)

    def get_missing(team):
        injured_lower = [p.lower() for p in injury_data.get(team, [])]
        result = []
        for k in IMPACT_PLAYERS.get(team, []):
            if k in SEASON_OUT or any(k in p for p in injured_lower):
                result.append((k, "out"))
            elif k in LIMITED_PLAYERS:
                result.append((k, "limited"))
        return result

    h_missing = get_missing(home)
    a_missing = get_missing(away)

    for p, status in h_missing:
        penalty = (SUPERSTAR_PENALTY if p in SUPERSTARS else STAR_PENALTY) if status == "out" else LIMITED_PENALTY
        h_stat["off"] -= penalty * 0.6
        h_stat["def"] += penalty * 0.4

    for p, status in a_missing:
        penalty = (SUPERSTAR_PENALTY if p in SUPERSTARS else STAR_PENALTY) if status == "out" else LIMITED_PENALTY
        a_stat["off"] -= penalty * 0.6
        a_stat["def"] += penalty * 0.4

    h_net  = (h_stat["off"] - h_stat["def"]) + h_base.get("form", 0.0)
    a_net  = (a_stat["off"] - a_stat["def"]) + a_base.get("form", 0.0)
    margin = (h_net - a_net) / 2 + HOME_ADVANTAGE

    def fmt(lst):
        return ["%s(%s)" % (display_player_name(p), "缺" if s == "out" else "限") for p, s in lst]

    return margin, fmt(h_missing), fmt(a_missing)


def predict_total(home, away, live_ratings):
    h_base = live_ratings.get(home, FALLBACK_RATINGS.get(home, DEFAULT_RATING))
    a_base = live_ratings.get(away, FALLBACK_RATINGS.get(away, DEFAULT_RATING))
    return round((h_base["off"] + a_base["off"]) / 2 * 2 * 0.97, 1)


def get_consensus_line(bookmakers, team_name):
    lines = []
    for book in bookmakers:
        for market in book.get("markets", []):
            if market.get("key") != "spreads":
                continue
            for outcome in market.get("outcomes", []):
                if normalize_team(outcome.get("name", "")) == team_name:
                    pt = outcome.get("point")
                    if pt is not None:
                        lines.append(pt)
    return (sum(lines) / len(lines)) if lines else None


def get_consensus_total(bookmakers):
    totals = []
    for book in bookmakers:
        for market in book.get("markets", []):
            if market.get("key") != "totals":
                continue
            for outcome in market.get("outcomes", []):
                if outcome.get("name", "").lower() == "over":
                    pt = outcome.get("point")
                    if pt is not None:
                        totals.append(pt)
    return (sum(totals) / len(totals)) if totals else None


def simulate_cover(blended, line):
    wins = sum(
        1 for _ in range(SIMS)
        if blended + random.gauss(0, DYNAMIC_STD_BASE) + line > 0
    )
    return wins / SIMS


def fetch_odds():
    params = {
        "apiKey":     ODDS_API_KEY,
        "regions":    "us",
        "markets":    "spreads,totals",
        "oddsFormat": "decimal",
    }
    data = safe_get(
        "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/",
        params=params,
    )
    if data is None:
        log.error("Odds API failed")
        return []
    log.info("Odds loaded: %d games", len(data))
    return data


def fetch_summer_league_sport_key():
    """Scan the live Odds API sports list for a basketball entry whose key or
    title mentions 'summer' -- the exact sport_key isn't documented and can
    change, so it's discovered instead of hardcoded."""
    data = safe_get(
        "https://api.the-odds-api.com/v4/sports/",
        params={"apiKey": ODDS_API_KEY, "all": "true"},
    )
    if not data:
        return None
    for sport in data:
        key   = (sport.get("key") or "").lower()
        title = (sport.get("title") or "").lower()
        group = (sport.get("group") or "").lower()
        if "basketball" not in group and "basketball" not in key:
            continue
        if "summer" in key or "summer" in title:
            log.info("Summer League sport_key discovered: %s", sport.get("key"))
            return sport.get("key")
    return None


def fetch_summer_league_odds():
    sport_key = fetch_summer_league_sport_key()
    if not sport_key:
        log.info("No NBA Summer League market currently listed on Odds API")
        return []
    data = safe_get(
        "https://api.the-odds-api.com/v4/sports/%s/odds/" % sport_key,
        params={
            "apiKey":     ODDS_API_KEY,
            "regions":    "us",
            "markets":    "h2h,spreads,totals",
            "oddsFormat": "decimal",
        },
    )
    return data or []


def fetch_summer_league_scores():
    """Try known ESPN league slugs in turn; the host city (and so the slug)
    moves year to year and isn't documented."""
    for slug in SUMMER_LEAGUE_ESPN_SLUGS:
        data = safe_get(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/%s/scoreboard" % slug
        )
        events = (data or {}).get("events") if data else None
        if events:
            log.info("Summer League scores loaded via ESPN slug '%s': %d events", slug, len(events))
            return events
    log.info("No ESPN Summer League scoreboard responded")
    return []


GAME_STATUS_ZH = {
    "final":       "已完賽",
    "scheduled":   "未開始",
    "in progress": "進行中",
    "halftime":    "中場休息",
    "postponed":   "延期",
    "canceled":    "取消",
    "cancelled":   "取消",
}

MARKET_ZH = {
    "h2h":     "獨贏",
    "spreads": "讓分",
    "totals":  "大小分",
}


def zh_team_name(name):
    """Best-effort English->Chinese team name translation, reusing the
    regular-season TEAM_CN map via normalize_team's substring match (handles
    ESPN's shorter Summer League display names like "Lakers" too). Falls
    back to the original string when nothing matches."""
    if not name:
        return name
    return TEAM_CN.get(normalize_team(name), name)


def zh_game_status(status):
    return GAME_STATUS_ZH.get((status or "").strip().lower(), status)


def analyze_summer_league():
    """Lightweight, informational-only Summer League report.

    Summer League rosters are dominated by rookies/two-way/G-League players
    and change daily, and the sample size per team is tiny (a handful of
    games), so unlike the regular-season model this deliberately does NOT
    run Kelly staking or bankroll sizing -- only a scoreboard, a simple
    point-margin power ranking, and a market watchlist for reference.
    """
    events     = fetch_summer_league_scores()
    odds_games = fetch_summer_league_odds()

    games   = []
    margins = {}
    for ev in events:
        comp = (ev.get("competitions") or [{}])[0]
        competitors = comp.get("competitors", [])
        if len(competitors) != 2:
            continue
        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
        h_name = zh_team_name((home.get("team") or {}).get("displayName", "?"))
        a_name = zh_team_name((away.get("team") or {}).get("displayName", "?"))
        try:
            h_score = int(home.get("score", 0) or 0)
            a_score = int(away.get("score", 0) or 0)
        except (TypeError, ValueError):
            h_score = a_score = 0
        status_type = (ev.get("status") or {}).get("type") or {}
        status      = zh_game_status(status_type.get("description") or "?")
        is_final    = bool(status_type.get("completed"))

        games.append({
            "status":      status,
            "home":        h_name,
            "away":        a_name,
            "home_score":  h_score,
            "away_score":  a_score,
            "start_time":  ev.get("date", ""),
        })

        if is_final and (h_score or a_score):
            margins.setdefault(h_name, []).append(h_score - a_score)
            margins.setdefault(a_name, []).append(a_score - h_score)

    power_ranking = [
        {"team": t, "games": len(v), "avg_margin": round(sum(v) / len(v), 1)}
        for t, v in margins.items()
    ]
    power_ranking.sort(key=lambda x: x["avg_margin"], reverse=True)

    watchlist = []
    for g in odds_games:
        try:
            c_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            continue
        home = zh_team_name(g.get("home_team", ""))
        away = zh_team_name(g.get("away_team", ""))
        for book in g.get("bookmakers", [])[:1]:
            for market in book.get("markets", []):
                if market.get("key") not in ("h2h", "spreads"):
                    continue
                for outcome in market.get("outcomes", []):
                    price = outcome.get("price")
                    if not price:
                        continue
                    watchlist.append({
                        "matchup":    "%s @ %s" % (away, home),
                        "start_time": c_time.isoformat() + "Z",
                        "market":     MARKET_ZH.get(market.get("key"), market.get("key")),
                        "pick":       zh_team_name(outcome.get("name")),
                        "point":      outcome.get("point"),
                        "price":      price,
                        "book":       book.get("title", "?"),
                    })

    return {
        "available":     bool(events or odds_games),
        "games":         games,
        "power_ranking": power_ranking[:10],
        "watchlist":     watchlist[:15],
        "note": "夏季聯賽陣容多為菜鳥/雙向合約球員，樣本數極小，僅供參考觀察，不計入 Kelly 資金配置。",
    }


def format_summer_league_section(sl):
    if not sl.get("available"):
        return "\n🏖️ **夏季聯賽**\n目前查無夏季聯賽賽事或盤口資料（可能尚未開打或資料源未提供）。\n"

    lines = ["\n🏖️ **NBA 夏季聯賽觀察**\n"]

    if sl["games"]:
        lines.append("📋 賽事:")
        for g in sl["games"][:10]:
            lines.append("> %s %d - %d %s (%s)" % (
                g["away"], g["away_score"], g["home_score"], g["home"], g["status"]
            ))

    if sl["power_ranking"]:
        lines.append("\n📈 戰力排行 (依已完賽場次平均分差):")
        for r in sl["power_ranking"][:8]:
            lines.append("> %s: %+.1f (%d 場)" % (r["team"], r["avg_margin"], r["games"]))

    if sl["watchlist"]:
        lines.append("\n👀 盤口觀察 (僅供參考，非資金建議):")
        for w in sl["watchlist"][:8]:
            point = (" %+.1f" % w["point"]) if w["point"] is not None else ""
            lines.append("> %s | %s%s @ %.2f (%s)" % (
                w["matchup"], w["pick"], point, w["price"], w["book"]
            ))

    lines.append("\n> %s\n" % sl["note"])
    return "\n".join(lines) + "\n"


def chunked_send(content, webhook):
    lines = content.split("\n")
    # A single line longer than the chunk limit would otherwise produce an
    # oversized chunk that Discord's 2000-char hard cap rejects outright,
    # silently dropping that part of the message.
    lines = [
        (line[: DISCORD_CHAR_LIMIT - 3] + "...") if len(line) > DISCORD_CHAR_LIMIT else line
        for line in lines
    ]
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


RESULT_ZH = {"win": "獲勝", "loss": "落敗", "pending": "待開獎"}

# .title() mis-cases the handful of keys with internal capitals or that are
# better known by an all-caps nickname; everything else title-cases fine.
PLAYER_DISPLAY_OVERRIDES = {
    "mccain":    "McCain",
    "mccollum":  "McCollum",
    "lamelo":    "LaMelo",
    "cp3":       "CP3",
}


def display_player_name(key):
    return PLAYER_DISPLAY_OVERRIDES.get(key, key.title())


def build_team_stars():
    """Reference list of each team's marquee players, for the web dashboard.
    Pulled straight from IMPACT_PLAYERS (only last names/handles are stored),
    so display names are title-cased rather than full names."""
    return [
        {"team": TEAM_CN.get(team, team), "players": [display_player_name(p) for p in players]}
        for team, players in IMPACT_PLAYERS.items()
    ]


def build_history_list(history, limit=30):
    items = sorted(history.values(), key=lambda h: h.get("date", ""), reverse=True)
    return [
        {
            "date":        h.get("date", ""),
            "bet":         h.get("bet", ""),
            "book":        h.get("book", ""),
            "price":       h.get("price"),
            "prob":        round(h.get("prob", 0) * 100, 1),
            "edge":        round(h.get("edge", 0) * 100, 1),
            "kelly_stake": h.get("kelly_stake"),
            "result":      RESULT_ZH.get(h.get("result", "pending"), h.get("result", "pending")),
        }
        for h in items[:limit]
    ]


def export_site_data(now_tw, data_source, is_official_run, daily_picks, today_s,
                      total_rec, wins, win_rate, profit, summer_league, history):
    """Write a JSON snapshot for the static web dashboard (docs/index.html)."""
    days = []
    for date in sorted(daily_picks):
        picks = sorted(daily_picks[date].values(), key=lambda x: x["edge"], reverse=True)
        days.append({
            "date":  date,
            "label": "今日賽事" if date == today_s else ("預告 %s" % date),
            "picks": [
                {
                    "tier":       p["tier"],
                    "matchup":    p["matchup"],
                    "start_time": p["start_time"],
                    "bet":        p["bet"],
                    "price":      p["price"],
                    "book":       p["book"],
                    "prob":       round(p["prob"] * 100, 1),
                    "edge":       round(p["edge"] * 100, 1),
                    "kelly_stake": p["kelly_stake"],
                    "missing":    p["missing"],
                    "consensus":  p["consensus"],
                    "ou_note":    p["ou_note"],
                }
                for p in picks
            ],
        })

    total_picks = sum(len(v) for v in daily_picks.values())
    avg_edge    = (
        sum(p["edge"] for d in daily_picks.values() for p in d.values()) / total_picks
        if total_picks else 0
    )

    payload = {
        "version":          VERSION,
        "generated_at":     now_tw.strftime("%Y-%m-%d %H:%M"),
        "data_source":      data_source,
        "run_type":         "official" if is_official_run else "test",
        "regular_season": {
            "total_picks": total_picks,
            "avg_edge":    round(avg_edge * 100, 1),
            "days":        days,
        },
        "performance": {
            "total_recommendations": total_rec,
            "wins":                  wins,
            "win_rate":              round(win_rate, 1),
            "profit":                round(profit, 1),
        },
        "summer_league": summer_league,
        "history":       build_history_list(history),
        "team_stars":    build_team_stars(),
    }

    try:
        os.makedirs(os.path.dirname(SITE_DATA_PATH) or ".", exist_ok=True)
        with open(SITE_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log.info("Site data written: %s", SITE_DATA_PATH)
    except OSError as e:
        log.error("Failed to write site data: %s", e)


def run():
    if not all([ODDS_API_KEY, WEBHOOK]):
        log.error("Missing env vars")
        return

    now_utc = datetime.utcnow()
    now_tw  = now_utc + timedelta(hours=8)
    today_s = now_tw.strftime("%Y-%m-%d")

    # GITHUB_EVENT_NAME ("schedule" vs "workflow_dispatch") is set automatically
    # by GitHub Actions and is a reliable signal. Falling back to a wall-clock
    # hour check (as before) only when that env var is absent -- e.g. running
    # locally -- since GH Actions cron runs can be delayed past the exact
    # minute/hour they were scheduled for, which silently turned "official"
    # runs into untracked ones under the old hour==22 check.
    github_event = os.getenv("GITHUB_EVENT_NAME", "")
    if github_event:
        is_official_run = (github_event == "schedule")
    else:
        is_official_run = (now_utc.hour == 22)
    log.info("Official run: %s (event: %s, UTC hour: %d)", is_official_run, github_event or "n/a", now_utc.hour)

    live_ratings = fetch_team_stats()
    data_source  = "即時數據" if live_ratings else "靜態備用"
    injuries     = get_injury_report()
    games        = fetch_odds()
    history      = load_history()
    summer_league = analyze_summer_league()

    if not games and not summer_league.get("available"):
        log.info("No regular-season games and no Summer League data; nothing to report")
        return

    daily_picks = {}

    for g in games:
        try:
            c_time_utc = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
            c_time_tw  = c_time_utc + timedelta(hours=8)
        except (KeyError, ValueError):
            continue

        if c_time_utc < now_utc:
            continue

        g_date     = c_time_tw.strftime("%Y-%m-%d")
        home       = normalize_team(g.get("home_team", ""))
        away       = normalize_team(g.get("away_team", ""))
        game_id    = "%s@%s_%s" % (away, home, g_date)
        bookmakers = g.get("bookmakers", [])

        daily_picks.setdefault(g_date, {})
        margin, h_missing, a_missing = predict_margin(home, away, injuries, live_ratings)

        model_total     = predict_total(home, away, live_ratings)
        consensus_total = get_consensus_total(bookmakers)
        ou_note = ""
        if consensus_total:
            diff = model_total - consensus_total
            if diff > 3:
                ou_note = "OU: 模型偏大分 (%.1f vs 市場 %.1f) 偏Over" % (model_total, consensus_total)
            elif diff < -3:
                ou_note = "OU: 模型偏小分 (%.1f vs 市場 %.1f) 偏Under" % (model_total, consensus_total)
            else:
                ou_note = "OU: 模型 %.1f vs 市場 %.1f (無明顯偏向)" % (model_total, consensus_total)

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
                    if not (MIN_PRICE < price <= MAX_PRICE):
                        continue

                    consensus = get_consensus_line(bookmakers, name)
                    if consensus is None:
                        consensus = line

                    # A higher signed point value is always better for whichever side
                    # you're betting: more cushion for the underdog (+5.5 beats +4.5),
                    # less to cover for the favorite (-4.5 beats -5.5). So the
                    # comparison is `line - consensus` uniformly -- no sign flip by
                    # favorite/underdog. (Previously flipped for negative lines, which
                    # inverted the favorable/unfavorable verdict for every favorite bet.)
                    line_advantage = line - consensus
                    if line_advantage < 0:
                        continue

                    target  = margin if name == home else -margin
                    blended = target * MODEL_WEIGHT + (-consensus) * MARKET_WEIGHT
                    prob    = simulate_cover(blended, line)
                    edge    = prob - (1 / price)

                    if edge < EDGE_THRESHOLD:
                        continue

                    missing = (h_missing if name == home else a_missing) + \
                              (a_missing if name == home else h_missing)

                    stake = kelly_stake(prob, price, BANKROLL)

                    if edge > 0.12:
                        tier = "💎 頂級"
                    elif edge > 0.09:
                        tier = "🔥 強力"
                    else:
                        tier = "⭐ 穩定"

                    bet_cn        = TEAM_CN.get(name, name)
                    away_cn       = TEAM_CN.get(away, away)
                    home_cn       = TEAM_CN.get(home, home)
                    missing_str   = "狀況: " + ", ".join(missing) if missing else "陣容完整"
                    consensus_str = "共識線: %+.1f" % consensus

                    msg = (
                        "**[%s] %s @ %s** (%s)\n"
                        "投注: `%s %+.1f` @ **%.2f** (%s)\n"
                        "> %s | %s\n"
                        "> 勝率: %.1f%% | Edge: %+.1f%% | Kelly建議: $%.1f\n"
                        "> %s\n"
                    ) % (
                        tier, away_cn, home_cn,
                        c_time_tw.strftime("%m/%d %H:%M"),
                        bet_cn, line, price, book.get("title", "?"),
                        missing_str, consensus_str,
                        prob * 100, edge * 100, stake,
                        ou_note,
                    )

                    existing = daily_picks[g_date].get(game_id)
                    if existing is None or edge > existing["edge"]:
                        daily_picks[g_date][game_id] = {
                            "edge":        edge,
                            "prob":        prob,
                            "price":       price,
                            "kelly_stake": stake,
                            "msg":         msg,
                            "tier":        tier,
                            "matchup":     "%s @ %s" % (away_cn, home_cn),
                            "start_time":  c_time_tw.strftime("%m/%d %H:%M"),
                            "bet":         "%s %+.1f" % (bet_cn, line),
                            "book":        book.get("title", "?"),
                            "missing":     missing_str,
                            "consensus":   consensus_str,
                            "ou_note":     ou_note,
                        }

                    if edge > 0.12 and is_official_run and g_date == today_s:
                        existing_h = history.get(game_id)
                        if existing_h is None or edge > existing_h.get("edge", 0):
                            history[game_id] = {
                                "date":        g_date,
                                "bet":         "%s %+.1f" % (TEAM_CN.get(name, name), line),
                                "book":        book.get("title", "?"),
                                "price":       price,
                                "prob":        round(prob, 4),
                                "edge":        round(edge, 4),
                                "kelly_stake": stake,
                                "result":      existing_h.get("result", "pending") if existing_h else "pending",
                            }

    total_rec, wins, win_rate, profit = calc_performance(history)
    perf_msg = (
        "\n📊 **歷史績效報告** (僅統計💎頂級)\n"
        "總推薦: %d 場 | 已結算: %d 場\n"
        "勝率: %.1f%% | 損益: %+.1f 元\n"
        "（以每場 Kelly 建議金額計算）\n"
    ) % (len(history), total_rec, win_rate, profit)

    total_picks = sum(len(v) for v in daily_picks.values())
    avg_edge    = (
        sum(p["edge"] for d in daily_picks.values() for p in d.values()) / total_picks
        if total_picks else 0
    )

    output = "🏀 NBA %s | 更新: %s | 資料: %s | 推薦: %d 場 | 平均Edge: %+.1f%%\n" % (
        VERSION, now_tw.strftime("%m/%d %H:%M"), data_source, total_picks, avg_edge * 100
    )

    if is_official_run:
        output += "📌 正式記錄版本\n"
    else:
        output += "🔧 測試版本（不寫入回測）\n"

    if not daily_picks:
        output += "\n今日無符合條件之推薦。\n"
    else:
        for date in sorted(daily_picks):
            label = "📅 今日賽事" if date == today_s else ("⏭ 預告 %s" % date)
            output += "\n%s\n" % label
            for p in sorted(daily_picks[date].values(), key=lambda x: x["edge"], reverse=True):
                output += p["msg"]
            output += "-" * 30 + "\n"

    output += perf_msg
    output += format_summer_league_section(summer_league)

    if is_official_run:
        save_history(history)
        log.info("History saved (official run, top tier only)")
    else:
        log.info("History NOT saved (test run)")

    export_site_data(
        now_tw=now_tw, data_source=data_source, is_official_run=is_official_run,
        daily_picks=daily_picks, today_s=today_s,
        total_rec=total_rec, wins=wins, win_rate=win_rate, profit=profit,
        summer_league=summer_league, history=history,
    )

    log.info("Sending to Discord, length: %d", len(output))
    chunked_send(output, WEBHOOK)
    log.info("Done")


if __name__ == "__main__":
    run()
