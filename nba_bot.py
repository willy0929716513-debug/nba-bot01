import requests
import os
import random
import logging
import json
import time
import re
import math
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

# Manually pinned per season (confirmed 2026-09-30 via the NBA's official
# schedule release: opening night is 2026-10-20) rather than computed, since
# the league doesn't tip off on a fixed formula-derivable date. The Odds
# API's basketball_nba feed does not distinguish preseason exhibition games
# from real regular-season games -- same sport_key, same field shape -- so
# without this cutoff, preseason games (wildly unpredictable: heavy rotation
# experimentation, stars resting, lines that don't mean what they normally
# mean) would flow straight into the same Kelly-staked, real-money
# recommendation pipeline as a real game. Must be updated each season ahead
# of opening night.
REGULAR_SEASON_START = datetime(2026, 10, 20)

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
# NBA game totals have no settled, backtested std of their own yet (same
# caveat as every other constant here) -- set a little above the margin's
# DYNAMIC_STD_BASE as a starting assumption, since a total sums two teams'
# independent scoring variance rather than one team's margin swing.
TOTAL_STD_BASE   = 15.0
MIN_TOTAL_PRICE  = 1.75
MAX_TOTAL_PRICE  = 2.15
DISCORD_CHAR_LIMIT = 1900
BANKROLL         = 1000.0
KELLY_FRACTION   = 0.20

# Below this many settled picks, a win-rate swings wildly on pure variance
# (e.g. 2/3 vs 1/3 look like a 33-point spread but are both just "one game
# different"), so any win-rate/edge-tier reporting under this count needs an
# explicit "too small to mean anything yet" caveat rather than being shown as
# a clean percentage that implies statistical confidence it doesn't have.
MIN_HISTORY_SAMPLE = 10

# Re-verified ahead of the 2026-27 season tipoff (Oct 20, 2026; last checked
# 2026-09-30) after the 2026-07 pass. Two moves resolved since then, both
# confirmed via multiple independent sources: the Kawhi Leonard/Brandon
# Ingram trade (agreed in June, held up by an NBA investigation into
# Clippers salary-cap violations) finally closed on 2026-09-14 -- Leonard to
# the Raptors, Ingram (plus Gradey Dick) to the Clippers -- and LeBron James,
# an unsigned free agent as of the last pass, signed a 2yr/$8M deal with the
# 76ers, so Philadelphia's third slot moves from Jaylen Brown to James (the
# bigger injury-report storyline of the two, and IMPACT_PLAYERS is capped at
# 3 per team). Carrying forward from 2026-07: Giannis Bucks->Heat, Jaylen
# Brown<->Paul George (Celtics/76ers), Ja Morant Grizzlies->Blazers, Jaren
# Jackson Jr. Grizzlies->Jazz, Santi Aldama Grizzlies->Mavericks, LaMelo Ball
# Hornets->Timberwolves, Julius Randle Timberwolves->Nets, Miles Bridges
# Hornets->Suns, Chris Paul retired.
#
# Full 30-team sweep completed 2026-09-30 (on top of the Warriors/Mavericks
# fixes already folded in above). Also fixed: Thunder ("mccain" -> "williams"
# -- Jalen Williams is OKC's clearly more central co-star, "totally healthy"
# for 2026-27 after an injury-limited prior season; McCain, real but a lesser
# piece, was crowding him out), Pacers (added "haliburton", dropping
# "nembhard" -- Tyrese Haliburton tore his Achilles in Game 7 of the 2025
# Finals, missed all of 2025-26, and is expected with no restrictions for
# the 2026-27 opener; omitting the returning franchise player was the same
# blind spot the pre-fix Warriors/Curry entry had), Bucks ("dieng" ->
# "ware" -- Kel'el Ware arrived in the Giannis-to-Miami return package and
# has a higher ceiling than Ousmane Dieng, a minor rotation piece). Every
# other team's 3 names were independently verified current and accurate.
#
# Flagged but NOT reflected in the data below, since it's a contract
# standoff rather than an injury and may resolve either way within days:
# Jalen Duren (Pistons) is holding out of training camp in an RFA
# extension dispute, with an Oct 1, 2026 deadline to sign his qualifying
# offer. RotoWire's injury report won't catch a holdout, so if this drags
# into the season, Duren's IMPACT_PLAYERS entry (once added) would need a
# manual availability check rather than relying on the usual scrape.
IMPACT_PLAYERS = {
    "Los Angeles Lakers":     ["doncic", "kessler", "reaves"],
    "Washington Wizards":     ["young", "davis", "sarr"],
    "Golden State Warriors":  ["curry", "butler", "porzingis"],
    "Cleveland Cavaliers":    ["harden", "mitchell", "mobley"],
    "Los Angeles Clippers":   ["ingram", "garland", "hachimura"],
    "Dallas Mavericks":       ["flagg", "irving", "lively"],
    "Boston Celtics":         ["george", "white", "queta"],
    "Denver Nuggets":         ["jokic", "murray", "gordon"],
    "Oklahoma City Thunder":  ["shai", "williams", "holmgren"],
    "San Antonio Spurs":      ["wembanyama", "fox", "castle"],
    "Milwaukee Bucks":        ["herro", "turner", "ware"],
    "New York Knicks":        ["brunson", "towns", "bridges"],
    "Houston Rockets":        ["durant", "sengun", "sheppard"],
    "Indiana Pacers":         ["haliburton", "siakam", "zubac"],
    "Philadelphia 76ers":     ["maxey", "embiid", "james"],
    "Minnesota Timberwolves": ["ball", "edwards", "gobert"],
    "Miami Heat":             ["adebayo", "giannis", "wiggins"],
    "Portland Trail Blazers": ["avdija", "clingan", "morant"],
    "Detroit Pistons":        ["cunningham", "duren", "thompson"],
    "Sacramento Kings":       ["sabonis", "monk", "keegan"],
    "Atlanta Hawks":          ["johnson", "daniels", "okongwu"],
    "Chicago Bulls":          ["giddey", "claxton", "powell"],
    "Charlotte Hornets":      ["white", "miller", "reid"],
    "Orlando Magic":          ["banchero", "suggs", "wagner"],
    "Toronto Raptors":        ["leonard", "quickley", "barnes"],
    "Memphis Grizzlies":      ["boozer", "edey", "coward"],
    "New Orleans Pelicans":   ["zion", "murphy", "murray"],
    "Utah Jazz":              ["markkanen", "george", "jackson"],
    "Brooklyn Nets":          ["porter", "randle", "sharpe"],
    "Phoenix Suns":           ["booker", "green", "brooks"],
}

# NOTE: these two sets are a *manual fallback* only, merged in when the live
# RotoWire scrape (get_injury_report) can't confirm a status on its own. They
# go stale every offseason/trade-deadline and must be re-verified against
# current injury reports before each new season. Last reviewed 2026-09-30:
# Jimmy Butler (Warriors) tore his ACL in Jan 2026 and is confirmed out well
# into 2026-27 (realistic earliest return Jan/Feb 2027), so he's populated
# here rather than left to RotoWire, since a scrape confirming an "out" is
# less reliable months in advance than this season's already-settled news.
# Kyrie Irving (Mavericks, ACL surgery last March) is NOT added despite also
# recovering -- unlike Butler, no source confirms he's out for a fixed
# stretch of 2026-27, so his day-to-day status is left to the live scrape
# instead of guessing a settled outcome that hasn't actually been decided.
SEASON_OUT = {"butler"}

LIMITED_PLAYERS = set()

SUPERSTARS = {
    "doncic", "jokic", "shai", "giannis", "durant",
    "harden", "embiid", "randle", "edwards",
    "wembanyama", "morant", "banchero", "young", "fox",
    "leonard", "james", "curry", "butler", "irving",
    "williams", "haliburton",
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

# For turning a stored bet string like "湖人 -5.5" back into an English team
# name when grading history against real final scores (which come from
# balldontlie, keyed by English name). Every TEAM_CN value is unique, so
# this reversal is lossless.
TEAM_CN_REVERSE = {zh: en for en, zh in TEAM_CN.items()}


# Substring matching below only catches full.lower() are literal substrings
# of one another, which misses common city abbreviations (e.g. "LA Clippers"
# is not a substring of "Los Angeles Clippers" -- "los" vs "la").
TEAM_ALIASES = {
    "la clippers": "los angeles clippers",
    "la lakers":   "los angeles lakers",
}


def normalize_team(name):
    if not name:
        return name
    n = name.lower()
    n = TEAM_ALIASES.get(n, n)
    for full in TEAM_CN:
        if n in full.lower() or full.lower() in n:
            return full
    return name


def _retry_after_seconds(response, attempt):
    """Respect the server's Retry-After header when present (common on
    429s); otherwise fall back to a growing backoff."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return 2.0 * attempt


def safe_get(url, headers=None, params=None, retries=3, timeout=15, max_wait=None):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            log.warning("Timeout attempt %d/%d: %s", attempt, retries, url)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            # 429 means "slow down", not "this will never work" -- unlike a
            # genuine 4xx (404/401/403), it's worth waiting out and retrying
            # rather than giving up on the first hit, which previously left
            # fetch_season_games()'s pagination loop stopping after just a
            # page or two once balldontlie's rate limit kicked in. But
            # blindly honoring whatever Retry-After the server sends can
            # itself make a caller unacceptably slow -- confirmed against a
            # real run where balldontlie asked for a 55s wait, twice, for a
            # fetch that's supposed to be one step in a dashboard update the
            # user is watching finish. max_wait caps what we're willing to
            # sit through per attempt; a caller that's fine waiting longer
            # (nothing currently needs that) can simply leave it unset.
            if status == 429 and attempt < retries:
                wait = _retry_after_seconds(e.response, attempt)
                if max_wait is not None:
                    wait = min(wait, max_wait)
                log.warning("Rate limited (429) attempt %d/%d: %s -- waiting %.1fs", attempt, retries, url, wait)
                time.sleep(wait)
                continue
            log.error("HTTP error %s: %s", status, url)
            break
        except Exception as e:
            log.warning("Request failed attempt %d/%d: %s", attempt, retries, e)
    return None


def _player_status(text, player, team_nickname, out_keywords, questionable_keywords, clear_keywords):
    """Scan every occurrence of `player` in `text`, not just the first, and
    return "out", "questionable", or None.

    Many players share a surname across the league (Davis, Brown, Williams,
    Green, Jones, ...), and IMPACT_PLAYERS only stores last names, so a
    single `text.find()` can lock onto an unrelated mention (a different
    player, a nav link, an unrelated story) and never look further. Each
    occurrence is only trusted if the team's nickname also appears nearby,
    which is how RotoWire groups players under a team heading.

    An occurrence tagged with a clear_keywords word ("available", "good to
    go" -- RotoWire's own all-clear wording, not a risk signal) is treated
    as no signal at all, not downgraded to "questionable". "out" from any
    occurrence wins outright and returns immediately, since it's strictly
    stronger evidence than "questionable" found elsewhere for the same
    player; "questionable" is only the final answer if no occurrence ever
    confirms "out".
    """
    start = 0
    best = None
    while True:
        idx = text.find(player, start)
        if idx == -1:
            return best
        start = idx + len(player)
        wide_window = text[max(0, idx - 300):idx + 300]
        if team_nickname not in wide_window:
            continue
        local_window = text[max(0, idx - 80):idx + 200]
        if any(s in local_window for s in clear_keywords):
            continue
        if any(s in local_window for s in out_keywords):
            return "out"
        if best is None and any(s in local_window for s in questionable_keywords):
            best = "questionable"


def get_injury_report():
    """Returns {"out": {team: [players]}, "questionable": {team: [players]}}.

    "questionable" used to be scanned for only to *suppress* false "out"
    matches (a mention near "questionable" was skipped rather than wrongly
    counted as out) and then thrown away -- a star tagged questionable got
    treated as fully healthy right up until RotoWire confirmed them out,
    even though real uncertainty about their availability existed well
    before that. Now that signal is kept and fed into predict_margin's
    existing "limited" penalty tier (LIMITED_PENALTY), which previously
    could only ever be populated by manually hardcoding a name into
    LIMITED_PLAYERS.
    """
    try:
        url  = "https://www.rotowire.com/basketball/injury-report.php"
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        r = requests.get(url, headers=hdrs, timeout=15)
        r.raise_for_status()

        out = {}
        questionable = {}
        text = r.text.lower()

        out_keywords           = ["ruled out", "will not play", "is out", "has been ruled out", "out ("]
        questionable_keywords  = ["questionable", "probable", "day-to-day"]
        clear_keywords         = ["available", "good to go"]

        for full_team in IMPACT_PLAYERS:
            nickname = full_team.split()[-1].lower()
            for player in IMPACT_PLAYERS[full_team]:
                if player not in text:
                    continue
                status = _player_status(text, player, nickname, out_keywords, questionable_keywords, clear_keywords)
                if status == "out" and player not in out.get(full_team, []):
                    out.setdefault(full_team, []).append(player)
                elif status == "questionable" and player not in questionable.get(full_team, []):
                    questionable.setdefault(full_team, []).append(player)

        for team, players in IMPACT_PLAYERS.items():
            for p in players:
                if p in SEASON_OUT and p not in out.get(team, []):
                    out.setdefault(team, []).append(p)

        log.info(
            "RotoWire injury loaded: %d out, %d questionable",
            sum(len(v) for v in out.values()), sum(len(v) for v in questionable.values()),
        )
        return {"out": out, "questionable": questionable}

    except Exception as e:
        log.warning("RotoWire failed: %s, using SEASON_OUT fallback", e)
        fallback = {}
        for team, players in IMPACT_PLAYERS.items():
            season_out = [p for p in players if p in SEASON_OUT]
            if season_out:
                fallback[team] = season_out
        return {"out": fallback, "questionable": {}}


def fetch_season_games():
    """Pull every game of the season (played and upcoming) from balldontlie.

    Reused for three things: building live_ratings, grading pending history
    entries against real final scores, and the "賽程" schedule view -- one
    paginated fetch instead of three separate ones.

    balldontlie's v1 API caps each response at 100 games and paginates via a
    `next_cursor` in the response `meta`. A single unpaginated call (as this
    used to be) only ever sees the first ~100 games of the ~1230-game season
    -- whatever the API's default ordering returns first, typically the
    earliest games -- so live_ratings would silently stay frozen at
    early-season form for the rest of the year regardless of how the season
    actually progressed. Page through with a generous cap (50 pages / 5000
    games) as a safety net against an infinite loop, not because a season
    could ever need that many.

    A full season needs ~13 pages, and balldontlie's rate limit is tight
    enough that firing those off back to back reliably draws a 429 a few
    pages in. safe_get() retries a 429 instead of giving up immediately --
    but confirmed against a real run, balldontlie's own Retry-After header
    asked for a 55-second wait, twice, adding 110+ seconds to a dashboard
    update the user is actively watching finish. max_wait caps what we're
    willing to sit through per attempt; if the quota hasn't recovered
    within that much shorter window either, better to give up for this run
    (falling back to FALLBACK_RATINGS, same as a missing key) than to sit
    through the server's full suggested wait on every page that hits it.
    """
    if not BALLDONTLIE_KEY:
        return []
    headers = {"Authorization": BALLDONTLIE_KEY}
    games  = []
    cursor = None
    for page in range(50):
        params = {"seasons[]": SEASON_YEAR, "per_page": 100}
        if cursor is not None:
            params["cursor"] = cursor
        if page > 0:
            time.sleep(1.0)
        data = safe_get(
            "https://api.balldontlie.io/v1/games", headers=headers, params=params,
            retries=3, max_wait=8,
        )
        if not data or "data" not in data:
            break
        games.extend(data["data"])
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return games


SRS_ITERATIONS = 10
FORM_GAMES     = 10
FORM_SCALE     = 0.5
FORM_CAP       = 3.0


def build_live_ratings(games):
    """Derive an off/def/form rating per team from this season's completed
    games so far. Separated from fetch_season_games() so the same raw game
    list can also feed grade_pending_history() and build_schedule() without
    fetching it three times.

    Previously this only looked at win/loss record (a team that goes 5-0
    winning every game by 1 point rated identically to one that goes 5-0
    blowing teams out by 20) and threw away the actual final scores
    balldontlie already hands over. Now it runs a Simple Rating System
    (SRS): each team's raw point-differential average gets iteratively
    corrected by the strength of the opponents it actually played, so a
    gaudy differential built against a weak schedule is discounted and a
    modest one against a tough schedule is credited -- this converges in a
    handful of passes. The resulting schedule adjustment is split evenly
    between "off" and "def" so predict_margin's existing off-minus-def net
    calculation reproduces the adjusted rating exactly, while predict_total
    still gets a separate offense and defense number to work with.

    "form" also switches from a win/loss-streak proxy to an actual
    scoring-margin trend: each team's last FORM_GAMES average margin versus
    its full-season average, capped so a short hot/cold streak nudges the
    prediction without dominating it.
    """
    team_games = {}
    for game in games:
        if game.get("status") != "Final":
            continue
        try:
            home = normalize_team(game["home_team"]["full_name"])
            away = normalize_team(game["visitor_team"]["full_name"])
            hs   = game["home_team_score"]
            vs   = game["visitor_team_score"]
            g_date = str(game.get("date", ""))[:10]
        except (KeyError, TypeError):
            continue
        if not hs or not vs:
            continue
        team_games.setdefault(home, []).append({"opp": away, "pf": hs, "pa": vs, "date": g_date})
        team_games.setdefault(away, []).append({"opp": home, "pf": vs, "pa": hs, "date": g_date})

    teams = [t for t in team_games if t in TEAM_CN]
    for t in teams:
        team_games[t].sort(key=lambda g: g["date"])

    raw_margin = {t: sum(g["pf"] - g["pa"] for g in team_games[t]) / len(team_games[t]) for t in teams}
    raw_off    = {t: sum(g["pf"] for g in team_games[t]) / len(team_games[t]) for t in teams}
    raw_def    = {t: sum(g["pa"] for g in team_games[t]) / len(team_games[t]) for t in teams}

    net = dict(raw_margin)
    for _ in range(SRS_ITERATIONS):
        net = {
            t: sum((g["pf"] - g["pa"]) + net.get(g["opp"], 0.0) for g in team_games[t]) / len(team_games[t])
            for t in teams
        }

    ratings = {}
    for t in teams:
        sos = net[t] - raw_margin[t]
        off = raw_off[t] + sos / 2
        deff = raw_def[t] - sos / 2

        recent = team_games[t][-FORM_GAMES:]
        recent_margin = sum(g["pf"] - g["pa"] for g in recent) / len(recent)
        form = max(-FORM_CAP, min(FORM_CAP, (recent_margin - raw_margin[t]) * FORM_SCALE))

        ratings[t] = {"off": round(off, 1), "def": round(deff, 1), "form": round(form, 2)}

    log.info("Game-based ratings loaded: %d teams", len(ratings))
    return ratings


def build_rest_days(games):
    """Map each team to every date (played or still scheduled) it appears
    on in this season's game list, so predict_margin can look up how many
    days of rest it has before a specific upcoming matchup. A team on zero
    days' rest (a back-to-back) is a well-documented, measurable
    disadvantage -- worse on the road than at home, due to the added
    travel -- and this is the first time that signal is available, since
    fetch_season_games() already pulls the full season schedule."""
    team_dates = {}
    for game in games:
        try:
            home = normalize_team(game["home_team"]["full_name"])
            away = normalize_team(game["visitor_team"]["full_name"])
        except (KeyError, TypeError):
            continue
        g_date = str(game.get("date", ""))[:10]
        if not g_date:
            continue
        team_dates.setdefault(home, set()).add(g_date)
        team_dates.setdefault(away, set()).add(g_date)
    return {team: sorted(dates) for team, dates in team_dates.items()}


def rest_days_before(team_dates, team, target_date):
    """Days of rest `team` has before `target_date` (YYYY-MM-DD), measured
    from its closest earlier game in the schedule. None if no earlier game
    is known (season opener, or the team is missing from the schedule
    feed) -- callers treat that as "unknown", not as any particular amount
    of rest."""
    dates = team_dates.get(team)
    if not dates:
        return None
    try:
        target = datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return None
    prev = None
    for d in dates:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            continue
        if dt < target and (prev is None or dt > prev):
            prev = dt
    return (target - prev).days if prev is not None else None


HOME_ADV_SHRINKAGE_K = 8


def build_home_advantage(games):
    """Measure this season's actual home-court edge from real results
    instead of assuming the fixed HOME_ADVANTAGE constant applies evenly
    to all 30 teams -- some arenas (altitude, crowd noise, travel distance
    for visitors) carry a measurably bigger home boost than others.

    Returns (league_avg, team_adv): league_avg is the league-wide average
    home-game margin, a data-derived replacement for the old fixed
    constant; team_adv maps each team to its own home-court edge, shrunk
    toward league_avg in proportion to how many home games it's actually
    played (HOME_ADV_SHRINKAGE_K games' worth of weight given to the
    league average) so a team 3 games into its home slate doesn't get a
    wild number off a couple of blowouts either way. Falls back to
    (HOME_ADVANTAGE, {}) when there's no game data yet."""
    home_margins = []
    team_home = {}
    for game in games:
        if game.get("status") != "Final":
            continue
        try:
            home = normalize_team(game["home_team"]["full_name"])
            hs = game["home_team_score"]
            vs = game["visitor_team_score"]
        except (KeyError, TypeError):
            continue
        if not hs or not vs:
            continue
        margin = hs - vs
        home_margins.append(margin)
        team_home.setdefault(home, []).append(margin)

    if not home_margins:
        return HOME_ADVANTAGE, {}

    league_avg = sum(home_margins) / len(home_margins)
    team_adv = {}
    for team, margins in team_home.items():
        if team not in TEAM_CN:
            continue
        n = len(margins)
        team_avg = sum(margins) / n
        shrink = n / (n + HOME_ADV_SHRINKAGE_K)
        team_adv[team] = league_avg + shrink * (team_avg - league_avg)
    return league_avg, team_adv


def describe_data_source(live_ratings, season_games):
    """Distinguish *why* live ratings fell back to FALLBACK_RATINGS, since
    "靜態備用" alone collapses three very different situations into one
    label: no key configured at all, a key that's configured but the new
    season's schedule/scores aren't in balldontlie yet (expected and
    harmless in the pre-season gap before REGULAR_SEASON_START), or a key
    that's configured but something else went wrong (API down, rate
    limited past the retry budget, etc.) -- the first is a setup gap worth
    fixing, the second needs no action at all, and the third is worth
    knowing is intermittent rather than assuming the setup is broken.
    """
    if live_ratings:
        return "即時數據"
    if not BALLDONTLIE_KEY:
        return "靜態備用（未設定 BALLDONTLIE_KEY）"
    if not season_games:
        return "靜態備用（暫時連不到 balldontlie，可能是賽季資料還沒上架或 API 異常）"
    return "靜態備用（賽季尚未開打，還沒有已完賽的比賽）"


def load_history():
    if not GITHUB_TOKEN:
        return {}
    headers = {"Authorization": "token %s" % GITHUB_TOKEN}
    # GitHub's gist list defaults to 30/page; since the history gist's
    # updated_at refreshes on every official run it normally sorts first
    # anyway, but requesting the max page size costs nothing and removes
    # the risk entirely if other gists on the account get touched a lot
    # during an off-season lull.
    gists = safe_get("https://api.github.com/gists", headers=headers, params={"per_page": 100})
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
    gists   = safe_get("https://api.github.com/gists", headers=headers, params={"per_page": 100})
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


def format_market_calibration_line(label, perf, calib, min_sample=None):
    """One report line summarizing a single market's (spread or total)
    settled-bet performance plus calibration -- shared by the Discord
    message and (via export_site_data) the website, so the two never
    drift out of sync on what a market's numbers actually mean."""
    min_sample = MIN_HISTORY_SAMPLE if min_sample is None else min_sample
    total, wins, win_rate, profit = perf
    line = "%s: %d 場已結算 | 勝率 %.1f%% | 損益 %+.1f 元\n" % (label, total, win_rate, profit)
    if calib["n"] > 0:
        line += "> Brier %.4f ｜ LogLoss %.4f" % (calib["brier"], calib["log_loss"])
        if calib["mae_margin"] is not None:
            line += " ｜ 分差 MAE %.1f" % calib["mae_margin"]
        if calib["mae_total"] is not None:
            line += " ｜ 總分 MAE %.1f" % calib["mae_total"]
        line += "\n"
    if total < min_sample:
        line += "（樣本數 < %d 場，統計僅供參考，不代表長期表現）\n" % min_sample
    return line


def build_period_report(history, period_label, cutoff_date):
    """A report block summarizing settled regular-season bets (both
    markets, broken out separately) with a `date` on or after
    cutoff_date (YYYY-MM-DD) -- reused for both the weekly and monthly
    recap, just with a different label and window, by reusing
    calc_performance()/calc_calibration() against a date-filtered subset
    of history rather than needing a separate date-range-aware code path
    inside those functions.

    Returns "" when nothing settled in the window at all, so callers can
    skip appending an empty section instead of posting a report that's
    just headers over nothing.
    """
    recent = {k: v for k, v in history.items() if v.get("date", "") >= cutoff_date}
    spread_perf  = calc_performance(recent, league="regular", market="spread")
    spread_calib = calc_calibration(recent, league="regular", market="spread")
    totals_perf  = calc_performance(recent, league="regular", market="total")
    totals_calib = calc_calibration(recent, league="regular", market="total")
    if not spread_calib["n"] and not totals_calib["n"]:
        return ""
    report = "\n%s\n" % period_label
    report += format_market_calibration_line("讓分", spread_perf, spread_calib)
    report += format_market_calibration_line("大小分", totals_perf, totals_calib)
    return report


def calc_performance(history, league="regular", market=None):
    """Entries carry a "league" tag ("regular" or "summer") so the two can be
    tracked side by side in one Gist without a regular-season Kelly-based
    profit figure ever getting diluted by summer-league reference-only picks
    that were never actually staked. Untagged entries (written before the
    "league" field existed) default to "regular" for backward compatibility.

    `market` optionally narrows further to "spread" or "total" (untagged
    entries, written before totals bets existed, default to "spread").
    Leaving it None keeps every existing call site's original behavior --
    both markets combined.
    """
    total = win = 0
    profit = 0.0
    for record in history.values():
        if record.get("league", "regular") != league:
            continue
        if market is not None and record.get("market", "spread") != market:
            continue
        if record.get("result") not in ["win", "loss"]:
            continue
        total += 1
        stake = record.get("kelly_stake") or 10.0
        if record["result"] == "win":
            win    += 1
            profit += stake * (record.get("price", 1.9) - 1)
        else:
            profit -= stake
    win_rate = (win / total * 100) if total else 0
    return total, win, win_rate, profit


def calc_calibration(history, league="regular", market=None):
    """Brier score, log loss, and (when available) mean absolute error of
    the model's predicted margin/total against what actually happened --
    computed only from settled (win/loss) bets, not pushes or pending
    ones.

    This is pure measurement, not a fitted calibrator: with the sample
    sizes this project has so far (tens, not the hundreds+ a calibrator
    needs), fitting something like Platt scaling or isotonic regression
    would just be overfitting noise. This intentionally stops at
    reporting the raw numbers plus a bucketed calibration table (does the
    ~70%-confidence bucket actually win ~70% of the time?) and leaves
    fitting an actual correction for once there's enough settled history
    to validate one against, not just tune it on.

    mae_margin/mae_total only reflect records that have both a
    predicted_* and actual_* field -- older settled bets, graded before
    those fields existed, are silently skipped for that stat only (they
    still count toward the Brier/log-loss sample, since prob/result were
    always stored).
    """
    outcomes = []
    margin_errors = []
    total_errors = []
    for record in history.values():
        if record.get("league", "regular") != league:
            continue
        if market is not None and record.get("market", "spread") != market:
            continue
        if record.get("result") not in ("win", "loss"):
            continue
        prob = record.get("prob")
        if prob is None:
            continue
        outcome = 1.0 if record["result"] == "win" else 0.0
        outcomes.append((prob, outcome))

        pm, am = record.get("predicted_margin"), record.get("actual_margin")
        if pm is not None and am is not None:
            margin_errors.append(abs(pm - am))
        pt, at = record.get("predicted_total"), record.get("actual_total")
        if pt is not None and at is not None:
            total_errors.append(abs(pt - at))

    n = len(outcomes)
    if n == 0:
        return {"n": 0, "brier": None, "log_loss": None, "mae_margin": None, "mae_total": None, "buckets": []}

    brier = sum((p - o) ** 2 for p, o in outcomes) / n

    eps = 1e-6
    log_loss = 0.0
    for p, o in outcomes:
        pc = min(max(p, eps), 1 - eps)
        log_loss += -(o * math.log(pc) + (1 - o) * math.log(1 - pc))
    log_loss /= n

    bucket_defs = [
        ("50-60%", 0.5, 0.6), ("60-70%", 0.6, 0.7), ("70-80%", 0.7, 0.8),
        ("80-90%", 0.8, 0.9), ("90%+",   0.9, 1.01),
    ]
    buckets = []
    for label, lo, hi in bucket_defs:
        bucket = [(p, o) for p, o in outcomes if lo <= p < hi]
        if not bucket:
            continue
        bn = len(bucket)
        buckets.append({
            "range":         label,
            "n":             bn,
            "avg_predicted": round(sum(p for p, _ in bucket) / bn * 100, 1),
            "actual_rate":   round(sum(o for _, o in bucket) / bn * 100, 1),
        })

    return {
        "n":         n,
        "brier":     round(brier, 4),
        "log_loss":  round(log_loss, 4),
        "mae_margin": round(sum(margin_errors) / len(margin_errors), 2) if margin_errors else None,
        "mae_total":  round(sum(total_errors) / len(total_errors), 2) if total_errors else None,
        "buckets":   buckets,
    }


def find_final_score(games, home_en, away_en, taiwan_date, window_days=1):
    """Find a specific matchup's final score in balldontlie's raw game list.

    Matched by team identity, not by an exact date string: the-odds-api's
    commence_time (already converted to Taiwan dates elsewhere in this
    file) and balldontlie's own `date` field are on different clocks.
    Every realistic NBA tip-off time (noon ET through 10:30pm PT, i.e.
    UTC-4 to UTC-8) lands on "US scheduling date + 1" once converted to
    Taiwan's fixed UTC+8 -- there's no NBA game time for which that isn't
    true -- so balldontlie's date is reliably `taiwan_date - 1 day`. Search
    a small window around that derived date anyway (rather than requiring
    an exact match) as a safety margin against an edge case this reasoning
    missed, and let the team-pair match (teams essentially never play each
    other twice within a couple of days) disambiguate.
    """
    try:
        center = datetime.strptime(taiwan_date, "%Y-%m-%d") - timedelta(days=1)
    except ValueError:
        return None
    for g in games:
        if g.get("status") != "Final":
            continue
        try:
            g_home = normalize_team(g["home_team"]["full_name"])
            g_away = normalize_team(g["visitor_team"]["full_name"])
            g_date = datetime.strptime(str(g.get("date", ""))[:10], "%Y-%m-%d")
        except (KeyError, TypeError, ValueError):
            continue
        if {g_home, g_away} != {home_en, away_en}:
            continue
        if abs((g_date - center).days) > window_days:
            continue
        hs, vs = g.get("home_team_score"), g.get("visitor_team_score")
        if hs is None or vs is None:
            continue
        return {"home": g_home, "away": g_away, "home_score": hs, "away_score": vs}
    return None


def grade_pending_history(history, games):
    """Automatically settle pending regular-season history entries against
    real final scores, replacing the previous manual-Gist-editing workflow.
    Summer League entries are left alone -- analyze_summer_league() stops
    fetching outside SUMMER_LEAGUE_MONTHS, so there's no live score source
    for them once the season's over; any still-pending summer picks just
    stay pending, which is accurate (we genuinely don't know).

    Besides win/loss/push, records the actual final score alongside the
    prediction that was made for it (prob/edge were already stored), so
    the history view doubles as a predicted-vs-actual comparison rather
    than just a scoreboard. Also stashes actual_margin (spread bets) or
    actual_total (totals bets) in the same home-relative / game-total
    frame as predicted_margin/predicted_total, so calc_calibration() can
    compute a real MAE instead of just win/loss.

    Totals entries are keyed "<game_id>_total" (see run()'s totals
    history-write block) specifically so they don't collide with a
    spread entry for the same game in the same dict; strip that suffix
    before parsing the embedded team/date info out of the key.
    """
    graded = 0
    for game_id, record in history.items():
        if record.get("league", "regular") != "regular":
            continue
        if record.get("result") != "pending":
            continue

        market  = record.get("market", "spread")
        base_id = game_id[:-len("_total")] if market == "total" and game_id.endswith("_total") else game_id

        try:
            teams_part, date_part = base_id.rsplit("_", 1)
            away_en, home_en = teams_part.split("@", 1)
        except ValueError:
            continue

        if market == "total":
            m = re.match(r"^(大分 Over|小分 Under)\s+([+-]?\d+(?:\.\d+)?)$", record.get("bet", ""))
            if not m:
                continue
            side_label, line_str = m.group(1), m.group(2)

            score = find_final_score(games, home_en, away_en, date_part)
            if score is None:
                continue

            line = float(line_str)
            actual_total = score["home_score"] + score["away_score"]
            diff = actual_total - line
            if diff == 0:
                record["result"] = "push"
            elif (side_label == "大分 Over") == (diff > 0):
                record["result"] = "win"
            else:
                record["result"] = "loss"
            record["actual_total"] = actual_total
            record["final_score"] = "%s %d - %d %s" % (
                TEAM_CN.get(away_en, away_en), score["away_score"],
                score["home_score"], TEAM_CN.get(home_en, home_en),
            )
            graded += 1
            continue

        m = re.match(r"^(.+?)\s+([+-]?\d+(?:\.\d+)?)$", record.get("bet", ""))
        if not m:
            continue
        bet_team_cn, line_str = m.group(1), m.group(2)
        bet_team_en = TEAM_CN_REVERSE.get(bet_team_cn)
        if bet_team_en not in (home_en, away_en):
            continue

        score = find_final_score(games, home_en, away_en, date_part)
        if score is None:
            continue

        line = float(line_str)
        if bet_team_en == home_en:
            margin_for_bet = score["home_score"] - score["away_score"]
        else:
            margin_for_bet = score["away_score"] - score["home_score"]
        cover_margin = margin_for_bet + line

        if cover_margin > 0:
            record["result"] = "win"
        elif cover_margin < 0:
            record["result"] = "loss"
        else:
            record["result"] = "push"
        record["actual_margin"] = score["home_score"] - score["away_score"]
        record["final_score"] = "%s %d - %d %s" % (
            TEAM_CN.get(away_en, away_en), score["away_score"],
            score["home_score"], TEAM_CN.get(home_en, home_en),
        )
        graded += 1

    if graded:
        log.info("Auto-graded %d pending history entries against final scores", graded)
    return graded


def kelly_stake(prob, price, bankroll, fraction=KELLY_FRACTION):
    b = price - 1
    if b <= 0:
        return 0.0
    q = 1 - prob
    k = (b * prob - q) / b
    k = max(0.0, k) * fraction
    return round(bankroll * k, 1)


B2B_HOME_PENALTY = 1.3
B2B_AWAY_PENALTY = 2.0


def predict_margin(home, away, injury_data, live_ratings, home_rest=None, away_rest=None, home_adv=None):
    h_base = live_ratings.get(home, FALLBACK_RATINGS.get(home, DEFAULT_RATING))
    a_base = live_ratings.get(away, FALLBACK_RATINGS.get(away, DEFAULT_RATING))
    h_stat = dict(h_base)
    a_stat = dict(a_base)

    def get_missing(team):
        out_lower = [p.lower() for p in injury_data.get("out", {}).get(team, [])]
        q_lower   = [p.lower() for p in injury_data.get("questionable", {}).get(team, [])]
        result = []
        for k in IMPACT_PLAYERS.get(team, []):
            if k in SEASON_OUT or any(k in p for p in out_lower):
                result.append((k, "out"))
            elif k in LIMITED_PLAYERS or any(k in p for p in q_lower):
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
    margin = (h_net - a_net) / 2 + (home_adv if home_adv is not None else HOME_ADVANTAGE)

    # Zero days' rest (a back-to-back) is a measurable fatigue hit -- worse
    # on the road, where there's travel on top of it. Unknown rest (None,
    # e.g. a season opener or a team missing from the schedule feed) is
    # left alone rather than assumed to be either fresh or tired.
    if home_rest == 1:
        margin -= B2B_HOME_PENALTY
    if away_rest == 1:
        margin += B2B_AWAY_PENALTY

    def fmt(lst):
        return ["%s(%s)" % (display_player_name(p), "缺" if s == "out" else "限") for p, s in lst]

    return margin, fmt(h_missing), fmt(a_missing)


def predict_total(home, away, live_ratings):
    """Each side's expected points is its own offense against the
    opponent's defense, not just the two offenses added together -- the
    previous formula ignored defense entirely, so a historically stingy
    defense never pulled a projected total down."""
    h_base = live_ratings.get(home, FALLBACK_RATINGS.get(home, DEFAULT_RATING))
    a_base = live_ratings.get(away, FALLBACK_RATINGS.get(away, DEFAULT_RATING))
    home_pts = (h_base["off"] + a_base["def"]) / 2
    away_pts = (a_base["off"] + h_base["def"]) / 2
    return round(home_pts + away_pts, 1)


def get_consensus_moneyline_prob(bookmakers, team_name):
    """Devigged win probability implied by the moneyline (h2h) market,
    averaged across books -- a second, more direct read on what the
    market thinks the win probability actually is, versus backing one out
    indirectly through the spread and our own simulate_cover() Gaussian
    assumption. Devig per book (normalize that book's own both-sides
    1/price to sum to 1) before averaging, so each book's own vig cancels
    out instead of surviving into the average.

    Purely informational -- displayed next to the model's own probability
    for comparison, but does NOT feed into prob/edge/Kelly anywhere in
    this file. The spread pipeline already sizes real money off its
    existing math, and there isn't yet enough settled history to tell
    whether blending this in would actually improve calibration rather
    than just add noise, so for now it stays a side-by-side comparison.

    None if no h2h market data is available for this game (most books
    post spreads more reliably than moneylines on every game).
    """
    probs = []
    for book in bookmakers:
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            raw = {}
            for outcome in market.get("outcomes", []):
                name  = normalize_team(outcome.get("name", ""))
                price = outcome.get("price")
                if price and price > 0:
                    raw[name] = 1.0 / price
            total = sum(raw.values())
            if total > 0 and team_name in raw:
                probs.append(raw[team_name] / total)
    return (sum(probs) / len(probs)) if probs else None


def get_consensus_line(bookmakers, team_name, exclude_book=None):
    """Average posted spread for `team_name` across books, as a baseline to
    check whether one specific book's line is unusually favorable ("value").
    Excludes `exclude_book` (the book actually being evaluated as a
    candidate) so that check isn't circular -- comparing a line against a
    consensus that already includes that same line waters down how
    different it really is from the rest of the market, more so the fewer
    books are posting."""
    lines = []
    for book in bookmakers:
        if exclude_book is not None and book.get("title") == exclude_book:
            continue
        for market in book.get("markets", []):
            if market.get("key") != "spreads":
                continue
            for outcome in market.get("outcomes", []):
                if normalize_team(outcome.get("name", "")) == team_name:
                    pt = outcome.get("point")
                    if pt is not None:
                        lines.append(pt)
    return (sum(lines) / len(lines)) if lines else None


def get_consensus_total(bookmakers, exclude_book=None):
    totals = []
    for book in bookmakers:
        if exclude_book is not None and book.get("title") == exclude_book:
            continue
        for market in book.get("markets", []):
            if market.get("key") != "totals":
                continue
            for outcome in market.get("outcomes", []):
                if outcome.get("name", "").lower() == "over":
                    pt = outcome.get("point")
                    if pt is not None:
                        totals.append(pt)
    return (sum(totals) / len(totals)) if totals else None


def simulate_cover(blended, line, std=DYNAMIC_STD_BASE):
    wins = sum(
        1 for _ in range(SIMS)
        if blended + random.gauss(0, std) + line > 0
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


# NBA Summer League runs roughly July (occasionally spilling into late June
# or early August across the various sites -- Vegas, Utah, Sacramento,
# California). Widened a month on each side of the typical July window
# rather than pinned exactly, so a year where a site starts a little early
# or late doesn't fall through the gate.
SUMMER_LEAGUE_MONTHS = {6, 7, 8}

SUMMER_EDGE_THRESHOLD  = 0.10   # regular season is 0.06 -- demand more edge given the noisier signal
SUMMER_MODEL_WEIGHT    = 0.30
SUMMER_MARKET_WEIGHT   = 0.70
SUMMER_STD_MULTIPLIER  = 1.3    # exhibition-game scoring swings more than real-season ball

# Pre-tournament prior for teams that haven't played a Summer League game yet,
# so the model has something better than a flat 0 to work with before results
# exist. Deliberately limited to 2026 lottery picks (1-14) only -- these are
# public, structured draft results (not day-to-day roster/lineup guesses),
# confirmed by AJ Dybantsa/Washington at #1, Cameron Boozer/Memphis at #3
# (matches the Grizzlies' IMPACT_PLAYERS entry), and the Pacers->Clippers
# pick-5 trade, all cross-checked against multiple independent sources.
# Magnitude tapers with pick order and stays well inside the range real
# avg_margin values take on, since a lottery pick is a much weaker signal
# than actual measured Summer League performance -- this only nudges the
# probability/edge estimate, it does NOT count toward has_form, so it can
# never by itself earn a game the confident "meets threshold" badge.
SUMMER_ROOKIE_PRIOR = {
    "巫師":   4.0,   # AJ Dybantsa, #1
    "爵士":   3.5,   # Darryn Peterson, #2
    "灰熊":   3.5,   # Cameron Boozer, #3
    "公牛":   3.0,   # Caleb Wilson, #4
    "快艇":   3.0,   # via Pacers trade, #5
    "籃網":   2.5,   # Mikel Brown Jr., #6
    "國王":   2.5,   # Darius Acuff Jr., #7
    "老鷹":   2.0,   # Kingston Flemings, #8
    "獨行俠": 2.0,   # Morez Johnson Jr., #9
    "公鹿":   2.0,   # Brayden Burries, #10
    "勇士":   1.5,   # Yaxel Lendeborg, #11
    "雷霆":   1.5,   # Aday Mara, #12
    "熱火":   1.5,   # Nate Ament, #13
    "黃蜂":   1.5,   # Hannes Steinbach, #14
}


def summer_recommendations(odds_games, team_power, now_utc=None):
    """Same edge-vs-market-consensus approach as the regular-season model
    (predict_margin's role is played by team_power, a simple avg-margin
    proxy from completed Summer League games), gated harder than regular
    season: wider simulated variance (rosters/rotations are far less
    settled), and no Kelly stake -- this is a probability/edge lean only,
    not a bankroll-sizing recommendation, given how thin the sample is.

    Games that have already tipped off by `now_utc` are skipped, same as
    the regular-season loop in run() -- Summer League games are scattered
    across a wide window (as late as ~11:30pm and as early as ~9am Taiwan
    time on the same "slate"), so a single daily run can land after some of
    that slate has already started or finished. Recommending -- and worse,
    writing to Gist history -- a "pick" for a game whose outcome may already
    be baked into its odds (or already decided) would be look-ahead bias,
    not a real forecast.

    A team with no completed games yet falls back first to SUMMER_ROOKIE_PRIOR
    (a lottery-pick-based estimate) and only then to a neutral 0, rather than
    skipping the game outright -- early in the tournament the teams with an
    upcoming line on the board and the teams that have already played often
    barely overlap, and gating on "both teams already have data" made this
    come back empty most of the time. has_form tracks whether the model
    actually had *real, played* form data to add beyond the market line --
    the draft-pick prior improves the probability/edge estimate but is a
    guess, not a measurement, so it does not set has_form on its own. A
    recommendation is only tagged as meeting the edge bar when there's real
    signal behind it -- a "good" edge that comes purely from finding a
    better-than-consensus line, with no team strength data at all, is a
    weaker claim than one backed by form and shouldn't be badged the same way.

    Unlike the regular-season model, this does NOT drop evaluated games
    that fall short of the edge bar -- every game with enough market data
    to evaluate is returned (best line per game), tagged with whether it
    clears SUMMER_EDGE_THRESHOLD, so "we looked and found nothing" is
    visibly distinguishable from "we couldn't evaluate this at all".
    """
    now_utc = now_utc or datetime.utcnow()
    picks = {}
    for g in odds_games:
        try:
            c_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            continue
        if c_time < now_utc:
            continue
        home = zh_team_name(g.get("home_team", ""))
        away = zh_team_name(g.get("away_team", ""))
        home_power = team_power.get(home)
        away_power = team_power.get(away)
        has_form   = home_power is not None and away_power is not None
        home_est   = home_power if home_power is not None else SUMMER_ROOKIE_PRIOR.get(home, 0.0)
        away_est   = away_power if away_power is not None else SUMMER_ROOKIE_PRIOR.get(away, 0.0)
        margin_est = home_est - away_est
        bookmakers = g.get("bookmakers", [])
        game_id    = "%s@%s_%s" % (away, home, c_time.date())

        for book in bookmakers:
            for market in book.get("markets", []):
                if market.get("key") != "spreads":
                    continue
                for outcome in market.get("outcomes", []):
                    raw_name = outcome.get("name", "")
                    name     = normalize_team(raw_name)
                    line     = outcome.get("point")
                    price    = outcome.get("price")
                    if line is None or not price:
                        continue
                    if not (MIN_PRICE < price <= MAX_PRICE):
                        continue

                    consensus = get_consensus_line(bookmakers, name, exclude_book=book.get("title"))
                    if consensus is None:
                        consensus = line
                    if line - consensus < 0:
                        continue

                    is_home = zh_team_name(raw_name) == home
                    target  = margin_est if is_home else -margin_est
                    blended = target * SUMMER_MODEL_WEIGHT + (-consensus) * SUMMER_MARKET_WEIGHT
                    prob    = simulate_cover(blended, line, std=DYNAMIC_STD_BASE * SUMMER_STD_MULTIPLIER)
                    edge    = prob - (1 / price)

                    existing = picks.get(game_id)
                    if existing is None or edge > existing["edge"]:
                        picks[game_id] = {
                            "matchup":        "%s @ %s" % (away, home),
                            "start_time":     c_time.isoformat() + "Z",
                            "bet":            "%s %+.1f" % (zh_team_name(raw_name), line),
                            "price":          price,
                            "book":           book.get("title", "?"),
                            "prob":           round(prob * 100, 1),
                            "edge":           round(edge * 100, 1),
                            "has_form":       has_form,
                            "meets_threshold": edge >= SUMMER_EDGE_THRESHOLD and has_form,
                        }

    return sorted(picks.values(), key=lambda x: (x["start_time"][:10], -x["edge"]))


def build_summer_league_summary(power_ranking):
    """Short auto-generated narrative highlighting notable teams so the
    Summer League section reads as an analysis, not just raw tables.
    Comparisons are only added when they'd point at a different team than
    the previous one already mentioned, to avoid repeating "X leads in
    everything" when the sample is this small (often 1-3 games/team)."""
    if not power_ranking:
        return ""
    top    = power_ranking[0]
    bottom = power_ranking[-1]
    best_off = max(power_ranking, key=lambda x: x["avg_pf"])
    best_def = min(power_ranking, key=lambda x: x["avg_pa"])

    parts = ["戰績最佳：%s（%d勝%d敗，場均淨勝 %+.1f）。" % (top["team"], top["wins"], top["losses"], top["avg_margin"])]
    if bottom["team"] != top["team"]:
        parts.append("目前墊底：%s（%d勝%d敗，場均淨勝 %+.1f）。" % (bottom["team"], bottom["wins"], bottom["losses"], bottom["avg_margin"]))
    if best_off["team"] not in (top["team"], bottom["team"]):
        parts.append("進攻火力最猛：%s（場均 %.1f 分）。" % (best_off["team"], best_off["avg_pf"]))
    if best_def["team"] not in (top["team"], bottom["team"], best_off["team"]):
        parts.append("防守最穩：%s（場均僅失 %.1f 分）。" % (best_def["team"], best_def["avg_pa"]))
    return "".join(parts)


def analyze_summer_league(now_utc=None):
    """Lightweight, informational-only Summer League report.

    Summer League rosters are dominated by rookies/two-way/G-League players
    and change daily, and the sample size per team is tiny (a handful of
    games), so unlike the regular-season model this deliberately does NOT
    run Kelly staking or bankroll sizing -- only a scoreboard, a simple
    point-margin power ranking, and a market watchlist for reference.
    """
    now_utc = now_utc or datetime.utcnow()
    if now_utc.month not in SUMMER_LEAGUE_MONTHS:
        # Skip the ESPN/Odds API round-trips entirely outside the window --
        # there is nothing there for ~9 months of the year (this now runs
        # daily all season, not just in July), and hitting a paid API's rate
        # limit for a guaranteed-empty result is pure waste.
        return {
            "available":       False,
            "games":           [],
            "power_ranking":   [],
            "summary":         "",
            "recommendations": [],
            "watchlist":       [],
            "note":            "現在不是夏季聯賽期間（通常在 6-8 月），暫停抓取以節省 API 額度。",
        }
    events     = fetch_summer_league_scores()
    odds_games = fetch_summer_league_odds()

    games      = []
    team_games = {}
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
            team_games.setdefault(h_name, []).append({"pf": h_score, "pa": a_score, "win": h_score > a_score})
            team_games.setdefault(a_name, []).append({"pf": a_score, "pa": h_score, "win": a_score > h_score})

    power_ranking = []
    for t, glist in team_games.items():
        n     = len(glist)
        wins  = sum(1 for g in glist if g["win"])
        avg_pf = sum(g["pf"] for g in glist) / n
        avg_pa = sum(g["pa"] for g in glist) / n
        power_ranking.append({
            "team":       t,
            "games":      n,
            "wins":       wins,
            "losses":     n - wins,
            "avg_margin": round(avg_pf - avg_pa, 1),
            "avg_pf":     round(avg_pf, 1),
            "avg_pa":     round(avg_pa, 1),
        })
    power_ranking.sort(key=lambda x: x["avg_margin"], reverse=True)
    summary = build_summer_league_summary(power_ranking)
    team_power      = {r["team"]: r["avg_margin"] for r in power_ranking}
    recommendations = summer_recommendations(odds_games, team_power, now_utc=now_utc)

    watchlist = []
    for g in odds_games:
        try:
            c_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            continue
        if c_time < now_utc:
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
        "available":       bool(events or odds_games),
        "games":           games,
        "power_ranking":   power_ranking[:10],
        "summary":         summary,
        "recommendations": recommendations[:8],
        "watchlist":       watchlist[:15],
        "note": "夏季聯賽陣容多為菜鳥/雙向合約球員，樣本數極小；下方列出所有已可評估的賽事，Edge ≥ 10% 才標記為推薦，未達門檻的也照樣顯示數字供參考，且一律不提供 Kelly 資金配置建議，下注金額請自行斟酌。",
    }


# Separate, deliberately lower bar than SUMMER_EDGE_THRESHOLD (10%, which only
# gates the confident "🎯推薦" badge) -- this just decides what's worth writing
# to history for later win/loss tracking, same cutoff as the regular-season
# recommendation bar (EDGE_THRESHOLD).
SUMMER_HISTORY_EDGE_THRESHOLD = 0.06


def record_summer_history(history, summer_league, is_official_run):
    """Track Summer League picks with edge >= 6% in the same history dict as
    regular-season picks (tagged "league": "summer" so calc_performance can
    keep the two separate -- see its docstring), so the manual win/loss
    correction workflow already used for regular-season history also covers
    Summer League. No kelly_stake: this model deliberately never sizes a
    bankroll bet, so there's nothing to record there.
    """
    if not is_official_run:
        return
    for r in summer_league.get("recommendations", []):
        if r["edge"] < SUMMER_HISTORY_EDGE_THRESHOLD * 100:
            continue
        date = r.get("start_time", "")[:10]
        if not date:
            continue
        game_id  = "summer_%s_%s" % (r["matchup"], date)
        existing = history.get(game_id)
        if existing is not None and r["edge"] <= existing.get("edge", 0) * 100:
            continue
        history[game_id] = {
            "date":        date,
            "bet":         r["bet"],
            "book":        r["book"],
            "price":       r["price"],
            "prob":        round(r["prob"] / 100, 4),
            "edge":        round(r["edge"] / 100, 4),
            "kelly_stake": None,
            "result":      existing.get("result", "pending") if existing else "pending",
            "league":      "summer",
        }


def format_summer_league_section(sl, now_utc):
    # Off-season: leave it out of the Discord message entirely rather than
    # showing an empty placeholder every single day for ~9 months of the
    # year -- the user asked to get Summer League out of the way while the
    # regular season runs, not just see it reported as perpetually absent.
    if now_utc.month not in SUMMER_LEAGUE_MONTHS:
        return ""
    if not sl.get("available"):
        return "\n🏖️ **夏季聯賽**\n目前查無夏季聯賽賽事或盤口資料（可能尚未開打或資料源未提供）。\n"

    lines = ["\n🏖️ **NBA 夏季聯賽觀察**\n"]

    if sl.get("summary"):
        lines.append("🔎 %s" % sl["summary"])

    if sl["games"]:
        lines.append("\n📋 賽事:")
        for g in sl["games"][:10]:
            lines.append("> %s %d - %d %s (%s)" % (
                g["away"], g["away_score"], g["home_score"], g["home"], g["status"]
            ))

    if sl["power_ranking"]:
        lines.append("\n📈 戰力排行 (戰績 / 場均淨勝分 / 攻防):")
        for r in sl["power_ranking"][:8]:
            lines.append("> %s: %d勝%d敗 | 淨勝 %+.1f | 攻 %.1f 防 %.1f" % (
                r["team"], r["wins"], r["losses"], r["avg_margin"], r["avg_pf"], r["avg_pa"]
            ))

    if sl.get("recommendations"):
        lines.append("\n🎯 夏聯推薦與觀察 (Edge ≥ 10% 才算正式推薦，無 Kelly 建議):")
        for r in sl["recommendations"][:8]:
            tag = "🎯推薦" if r.get("meets_threshold") else "👀觀察中"
            lines.append("> [%s] %s | 投注 %s @ %.2f (%s) | 勝率 %.1f%% | Edge %+.1f%%" % (
                tag, r["matchup"], r["bet"], r["price"], r["book"], r["prob"], r["edge"]
            ))

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


RESULT_ZH = {"win": "獲勝", "loss": "落敗", "pending": "待開獎", "push": "走盤"}

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



def build_schedule(odds_games, balldontlie_games, today_s, now_utc, daily_picks, daily_totals=None):
    """Every regular-season game on the next upcoming slate, not just the
    ones with a qualifying recommendation -- a full card so the dashboard's
    "賽程" tab answers "what's coming up" by itself. Final scores are filled
    in via find_final_score() once a game's actually finished (relevant
    when this runs from a manual mid-day/evening trigger rather than the
    early-morning scheduled run).

    "Next upcoming slate" is not always literally today_s: the-odds-api
    only lists current/upcoming games, and a US evening slate converts to
    *tomorrow's* Taiwan date (see REGULAR_SEASON_START's note on the same
    ET-to-PT-vs-UTC+8 gap) -- so on a typical early-morning run, today_s
    itself usually has nothing left to show, and locking the schedule to
    that exact date made it come back empty on literally the season's
    opening night. Picks whichever date is nearest at or after today_s
    that actually has games listed, falling back to today_s itself (an
    empty result) only if nothing at all is upcoming.
    """
    dates_present = set()
    for g in odds_games:
        try:
            c_time_utc = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            continue
        dates_present.add((c_time_utc + timedelta(hours=8)).strftime("%Y-%m-%d"))
    candidates  = [d for d in dates_present if d >= today_s]
    target_date = min(candidates) if candidates else today_s
    picks_for_date = {
        gid for gid, p in daily_picks.get(target_date, {}).items() if p["meets_threshold"]
    }
    if daily_totals:
        picks_for_date |= {
            gid for gid, p in daily_totals.get(target_date, {}).items() if p["meets_threshold"]
        }

    out  = []
    seen = set()
    for g in odds_games:
        try:
            c_time_utc = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            continue
        c_time_tw = c_time_utc + timedelta(hours=8)
        g_date    = c_time_tw.strftime("%Y-%m-%d")
        if g_date != target_date:
            continue
        home_en = normalize_team(g.get("home_team", ""))
        away_en = normalize_team(g.get("away_team", ""))
        game_id = "%s@%s_%s" % (away_en, home_en, g_date)
        if game_id in seen:
            continue
        seen.add(game_id)

        score = find_final_score(balldontlie_games, home_en, away_en, g_date)
        if score:
            status = "已完賽"
            home_score, away_score = score["home_score"], score["away_score"]
        elif c_time_utc < now_utc:
            status = "進行中/比分未更新"
            home_score = away_score = None
        else:
            status = "未開始"
            home_score = away_score = None

        out.append({
            "home":       TEAM_CN.get(home_en, home_en),
            "away":       TEAM_CN.get(away_en, away_en),
            "start_time": c_time_tw.strftime("%m/%d %H:%M"),
            "status":     status,
            "home_score": home_score,
            "away_score": away_score,
            "has_pick":   game_id in picks_for_date,
        })
    out.sort(key=lambda x: x["start_time"])
    return {"date": target_date, "games": out}


MAX_PARLAY_LEGS = 3


def build_parlay(picks_for_date):
    """Combine same-day picks into a single parlay (串關) suggestion, built
    only from the 💎頂級 tier and capped at MAX_PARLAY_LEGS legs, taking the
    highest-probability ones first. The explicit design goal here is
    "least likely to blow up", not "biggest possible payout", so this
    deliberately does NOT throw every top-tier pick of the day into one
    bet -- more legs means more chances for exactly one of them to miss
    and sink the whole ticket.

    Every leg must still hit for a parlay to pay out at all, so even one
    built entirely from individually-confident picks is mechanically less
    safe than any single leg alone -- that's inherent to what a parlay is,
    not something leg selection can avoid, so it's spelled out in the
    output rather than left implied by "most stable."
    """
    top_tier = [p for p in picks_for_date if p.get("tier") == "💎 頂級"]
    if len(top_tier) < 2:
        return None
    top_tier.sort(key=lambda p: p["prob"], reverse=True)
    legs = top_tier[:MAX_PARLAY_LEGS]

    combined_price = 1.0
    combined_prob  = 1.0
    for p in legs:
        combined_price *= p["price"]
        combined_prob  *= p["prob"]

    return {
        "legs": [
            {
                "matchup": p["matchup"],
                "bet":     p["bet"],
                "price":   p["price"],
                "prob":    round(p["prob"] * 100, 1),
            }
            for p in legs
        ],
        "combined_price": round(combined_price, 2),
        "combined_prob":  round(combined_prob * 100, 1),
        "note": "每一腿都要命中才會中。即使每腿個別勝率都高，串關整體風險仍遠高於單場下注——這是串關機制本身的限制，不是選腿方式能避免的，請自行斟酌風險。",
    }


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
            "league":      h.get("league", "regular"),
            "market":      h.get("market", "spread"),
            "final_score": h.get("final_score"),
        }
        for h in items[:limit]
    ]


def build_performance_trend(history):
    """Chronological running win-rate and cumulative profit across settled
    (win/loss) regular-season bets, for the dashboard's performance trend
    chart -- same profit formula as calc_performance, just accumulated
    point by point instead of collapsed into one final total."""
    settled = [
        h for h in history.values()
        if h.get("result") in ("win", "loss") and h.get("league", "regular") == "regular"
    ]
    settled.sort(key=lambda h: h.get("date", ""))
    trend = []
    wins = 0
    profit = 0.0
    for i, h in enumerate(settled, 1):
        stake = h.get("kelly_stake") or 10.0
        if h["result"] == "win":
            wins += 1
            profit += stake * (h.get("price", 1.9) - 1)
        else:
            profit -= stake
        trend.append({
            "date":     h.get("date", ""),
            "win_rate": round(wins / i * 100, 1),
            "profit":   round(profit, 1),
        })
    return trend


def build_power_rankings(live_ratings):
    """Rank order for the dashboard's team power-rankings table: net rating
    (off minus def, already SRS opponent-adjusted by build_live_ratings)
    descending. Empty when live_ratings itself is empty (static-fallback
    mode), same as every other feature that depends on real game data."""
    rows = []
    for team, r in live_ratings.items():
        rows.append({
            "team": TEAM_CN.get(team, team),
            "net":  round(r["off"] - r["def"], 1),
            "off":  round(r["off"], 1),
            "def":  round(r["def"], 1),
            "form": round(r.get("form", 0.0), 2),
        })
    rows.sort(key=lambda x: x["net"], reverse=True)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


ELO_INITIAL = 1500.0
ELO_K       = 20.0


def build_elo_ratings(games):
    """Classic sequential Elo, replayed walk-forward over this season's
    completed games in chronological order -- each team starts at
    ELO_INITIAL, and a team's rating after processing date X depends only
    on games on or before X, never on anything later. That makes this
    inherently leak-free without needing special-casing: sorting by date
    before the single forward pass is the whole mechanism.

    Tracked as a second, independent rating system alongside
    build_live_ratings()' SRS approach -- not blended into picks or used
    to override them. There isn't yet enough settled history in this
    project's own Gist record to tell whether Elo or SRS actually
    correlates better with real outcomes here, so for now both are just
    reported side by side on the dashboard for comparison; K=20 and the
    1500 starting point are the standard textbook defaults, not values
    tuned against this project's own results.
    """
    completed = []
    for game in games:
        if game.get("status") != "Final":
            continue
        try:
            home = normalize_team(game["home_team"]["full_name"])
            away = normalize_team(game["visitor_team"]["full_name"])
            hs   = game["home_team_score"]
            vs   = game["visitor_team_score"]
            g_date = str(game.get("date", ""))[:10]
        except (KeyError, TypeError):
            continue
        if not hs or not vs:
            continue
        completed.append({"home": home, "away": away, "hs": hs, "vs": vs, "date": g_date})
    completed.sort(key=lambda g: g["date"])

    ratings = {}
    for g in completed:
        if g["home"] not in TEAM_CN or g["away"] not in TEAM_CN:
            continue
        r_home = ratings.setdefault(g["home"], ELO_INITIAL)
        r_away = ratings.setdefault(g["away"], ELO_INITIAL)
        expected_home = 1.0 / (1.0 + 10 ** ((r_away - r_home) / 400.0))
        actual_home   = 1.0 if g["hs"] > g["vs"] else 0.0
        delta = ELO_K * (actual_home - expected_home)
        ratings[g["home"]] = r_home + delta
        ratings[g["away"]] = r_away - delta

    return {team: round(r, 1) for team, r in ratings.items()}


def build_elo_rankings(elo_ratings):
    """Rank order for the dashboard, mirroring build_power_rankings()'
    shape so the frontend can render them the same way."""
    rows = [{"team": TEAM_CN.get(team, team), "rating": rating} for team, rating in elo_ratings.items()]
    rows.sort(key=lambda x: x["rating"], reverse=True)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def export_site_data(now_tw, data_source, is_official_run, daily_picks, today_s,
                      total_rec, wins, win_rate, profit, summer_league, history,
                      schedule, parlay, power_rankings, daily_totals=None, elo_rankings=None):
    """Write a JSON snapshot for the static web dashboard (docs/index.html)."""
    daily_totals = daily_totals or {}
    days = []
    for date in sorted(set(daily_picks) | set(daily_totals)):
        picks  = sorted(daily_picks.get(date, {}).values(), key=lambda x: x["edge"], reverse=True)
        totals = sorted(daily_totals.get(date, {}).values(), key=lambda x: x["edge"], reverse=True)
        days.append({
            "date":  date,
            "label": "今日賽事" if date == today_s else ("預告 %s" % date),
            "picks": [
                {
                    "tier":             p["tier"],
                    "meets_threshold":  p["meets_threshold"],
                    "matchup":          p["matchup"],
                    "start_time":       p["start_time"],
                    "bet":              p["bet"],
                    "price":            p["price"],
                    "book":             p["book"],
                    "prob":             round(p["prob"] * 100, 1),
                    "edge":             round(p["edge"] * 100, 1),
                    "kelly_stake":      p["kelly_stake"],
                    "missing":          p["missing"],
                    "consensus":        p["consensus"],
                    "ou_note":          p["ou_note"],
                    "market_implied_prob": (
                        round(p["market_implied_prob"] * 100, 1)
                        if p.get("market_implied_prob") is not None else None
                    ),
                }
                for p in picks
            ],
            "totals": [
                {
                    "tier":             t["tier"],
                    "meets_threshold":  t["meets_threshold"],
                    "matchup":          t["matchup"],
                    "start_time":       t["start_time"],
                    "bet":              t["bet"],
                    "price":            t["price"],
                    "book":             t["book"],
                    "prob":             round(t["prob"] * 100, 1),
                    "edge":             round(t["edge"] * 100, 1),
                    "kelly_stake":      t["kelly_stake"],
                    "model_total":      t["model_total"],
                    "market_total":     t["market_total"],
                }
                for t in totals
            ],
        })

    qualifying_picks = [p for d in daily_picks.values() for p in d.values() if p["meets_threshold"]] + \
                        [p for d in daily_totals.values() for p in d.values() if p["meets_threshold"]]
    total_picks = len(qualifying_picks)
    avg_edge    = (
        sum(p["edge"] for p in qualifying_picks) / total_picks
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
            "min_sample":            MIN_HISTORY_SAMPLE,
            "by_market": {
                m: {
                    "performance": {
                        "total_recommendations": t,
                        "wins":                  w,
                        "win_rate":              round(wr, 1),
                        "profit":                round(pf, 1),
                    },
                    "calibration": calc_calibration(history, league="regular", market=m),
                }
                for m, (t, w, wr, pf) in (
                    ("spread", calc_performance(history, league="regular", market="spread")),
                    ("total",  calc_performance(history, league="regular", market="total")),
                )
            },
        },
        "summer_league":      summer_league,
        "history":            build_history_list(history),
        "performance_trend":  build_performance_trend(history),
        "schedule":           schedule,
        "parlay":             parlay,
        "power_rankings":     power_rankings,
        "elo_rankings":       elo_rankings or [],
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
        is_official_run = (now_utc.hour == 14)
    log.info("Official run: %s (event: %s, UTC hour: %d)", is_official_run, github_event or "n/a", now_utc.hour)

    season_games = fetch_season_games()
    live_ratings = build_live_ratings(season_games)
    team_dates   = build_rest_days(season_games)
    league_home_adv, team_home_adv = build_home_advantage(season_games)
    data_source  = describe_data_source(live_ratings, season_games)
    injuries     = get_injury_report()
    games        = fetch_odds()
    history      = load_history()
    grade_pending_history(history, season_games)
    summer_league = analyze_summer_league(now_utc=now_utc)
    record_summer_history(history, summer_league, is_official_run)

    if not games and not summer_league.get("available"):
        log.info("No regular-season games and no Summer League data; nothing to report")
        return

    daily_picks  = {}
    daily_totals = {}

    for g in games:
        try:
            c_time_utc = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
            c_time_tw  = c_time_utc + timedelta(hours=8)
        except (KeyError, ValueError):
            continue

        if c_time_utc < now_utc:
            continue
        if c_time_utc < REGULAR_SEASON_START:
            continue

        g_date     = c_time_tw.strftime("%Y-%m-%d")
        home       = normalize_team(g.get("home_team", ""))
        away       = normalize_team(g.get("away_team", ""))
        game_id    = "%s@%s_%s" % (away, home, g_date)
        bookmakers = g.get("bookmakers", [])
        away_cn    = TEAM_CN.get(away, away)
        home_cn    = TEAM_CN.get(home, home)

        daily_picks.setdefault(g_date, {})
        daily_totals.setdefault(g_date, {})
        home_rest = rest_days_before(team_dates, home, g_date)
        away_rest = rest_days_before(team_dates, away, g_date)
        home_adv  = team_home_adv.get(home, league_home_adv)
        margin, h_missing, a_missing = predict_margin(
            home, away, injuries, live_ratings,
            home_rest=home_rest, away_rest=away_rest, home_adv=home_adv,
        )

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

                    # Bad/missing data only (not a strategy choice) -- can't
                    # price a bet at all without a usable price.
                    if not price or price <= 0:
                        continue

                    consensus = get_consensus_line(bookmakers, name, exclude_book=book.get("title"))
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

                    # MIN_SPREAD/MAX_SPREAD and MIN_PRICE/MAX_PRICE used to be a
                    # hard `continue` here, which meant a game with no outcome
                    # inside those bounds (a near-pick'em spread under 3, or a
                    # lopsided mismatch over 15) got NO entry at all -- "every
                    # game gets its best bet" silently didn't hold for those.
                    # They now only gate whether a bet counts as a confident,
                    # on-strategy recommendation, same role EDGE_THRESHOLD plays.
                    in_range = (MIN_SPREAD <= abs(line) <= MAX_SPREAD) and (MIN_PRICE < price <= MAX_PRICE)
                    meets_threshold = in_range and edge >= EDGE_THRESHOLD

                    missing = (h_missing if name == home else a_missing) + \
                              (a_missing if name == home else h_missing)

                    stake = kelly_stake(prob, price, BANKROLL)

                    if not meets_threshold:
                        tier = "👀 觀察中"
                    elif edge > 0.12:
                        tier = "💎 頂級"
                    elif edge > 0.09:
                        tier = "🔥 強力"
                    else:
                        tier = "⭐ 穩定"

                    bet_cn        = TEAM_CN.get(name, name)
                    missing_str   = "狀況: " + ", ".join(missing) if missing else "陣容完整"
                    consensus_str = "共識線: %+.1f" % consensus
                    market_implied_prob = get_consensus_moneyline_prob(bookmakers, name)

                    market_prob_note = (
                        "\n> 市場隱含勝率（moneyline）: %.1f%%（純比較，未納入計算）" % (market_implied_prob * 100)
                        if market_implied_prob is not None else ""
                    )
                    msg = (
                        "**[%s] %s @ %s** (%s)\n"
                        "投注: `%s %+.1f` @ **%.2f** (%s)\n"
                        "> %s | %s\n"
                        "> 勝率: %.1f%% | Edge: %+.1f%% | Kelly建議: $%.1f%s\n"
                        "> %s\n"
                    ) % (
                        tier, away_cn, home_cn,
                        c_time_tw.strftime("%m/%d %H:%M"),
                        bet_cn, line, price, book.get("title", "?"),
                        missing_str, consensus_str,
                        prob * 100, edge * 100, stake, market_prob_note,
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
                            "meets_threshold": meets_threshold,
                            "matchup":     "%s @ %s" % (away_cn, home_cn),
                            "start_time":  c_time_tw.strftime("%m/%d %H:%M"),
                            "bet":         "%s %+.1f" % (bet_cn, line),
                            "book":        book.get("title", "?"),
                            "missing":     missing_str,
                            "consensus":   consensus_str,
                            "ou_note":     ou_note,
                            "market_implied_prob": market_implied_prob,
                        }

                    if in_range and edge > 0.12 and is_official_run and g_date == today_s:
                        existing_h = history.get(game_id)
                        if existing_h is None or edge > existing_h.get("edge", 0):
                            history[game_id] = {
                                "date":             g_date,
                                "market":           "spread",
                                "bet":              "%s %+.1f" % (TEAM_CN.get(name, name), line),
                                "book":             book.get("title", "?"),
                                "price":            price,
                                "prob":             round(prob, 4),
                                "edge":             round(edge, 4),
                                "kelly_stake":      stake,
                                "predicted_margin": round(margin, 2),
                                "result":           existing_h.get("result", "pending") if existing_h else "pending",
                            }

        # Same edge-vs-market approach as spreads, applied to the Over/Under
        # total instead of the margin -- predict_total()/get_consensus_total()
        # were already being computed above for the informational ou_note,
        # but that number never became an actual recommendable bet with its
        # own edge/probability/Kelly stake until now. Kept as a separate
        # daily_totals track rather than merged into daily_picks, since a
        # total isn't tied to either team the way a spread pick is, and a
        # game can reasonably have both a spread pick and a totals pick at
        # once. Not yet wired into the Gist history/auto-grading -- that
        # expects a "TeamName +/-line" bet string, which a totals pick isn't.
        for book in bookmakers:
            for market in book.get("markets", []):
                if market.get("key") != "totals":
                    continue
                book_consensus_total = get_consensus_total(bookmakers, exclude_book=book.get("title"))
                for outcome in market.get("outcomes", []):
                    side = outcome.get("name", "").lower()
                    if side not in ("over", "under"):
                        continue
                    total_line = outcome.get("point")
                    t_price    = outcome.get("price", 0)
                    if total_line is None or not t_price or t_price <= 0:
                        continue

                    t_consensus = book_consensus_total if book_consensus_total is not None else total_line
                    # Over wants the lowest available line (easier to clear);
                    # Under wants the highest (easier to stay below) -- the
                    # opposite of each other, unlike a spread's single
                    # "higher point value always better" rule.
                    total_advantage = (t_consensus - total_line) if side == "over" else (total_line - t_consensus)
                    if total_advantage < 0:
                        continue

                    prob_over = simulate_cover(model_total - total_line, 0, std=TOTAL_STD_BASE)
                    t_prob    = prob_over if side == "over" else (1 - prob_over)
                    t_edge    = t_prob - (1 / t_price)

                    t_in_range = MIN_TOTAL_PRICE < t_price <= MAX_TOTAL_PRICE
                    t_meets_threshold = t_in_range and t_edge >= EDGE_THRESHOLD

                    t_stake = kelly_stake(t_prob, t_price, BANKROLL)

                    if not t_meets_threshold:
                        t_tier = "👀 觀察中"
                    elif t_edge > 0.12:
                        t_tier = "💎 頂級"
                    elif t_edge > 0.09:
                        t_tier = "🔥 強力"
                    else:
                        t_tier = "⭐ 穩定"

                    side_cn = "大分 Over" if side == "over" else "小分 Under"
                    t_msg = (
                        "**[%s] %s @ %s** (%s)\n"
                        "投注: `%s %.1f` @ **%.2f** (%s)\n"
                        "> 模型預測 %.1f 分 | 共識 %.1f 分\n"
                        "> 機率: %.1f%% | Edge: %+.1f%% | Kelly建議: $%.1f\n"
                    ) % (
                        t_tier, away_cn, home_cn,
                        c_time_tw.strftime("%m/%d %H:%M"),
                        side_cn, total_line, t_price, book.get("title", "?"),
                        model_total, t_consensus,
                        t_prob * 100, t_edge * 100, t_stake,
                    )

                    existing_t = daily_totals[g_date].get(game_id)
                    if existing_t is None or t_edge > existing_t["edge"]:
                        daily_totals[g_date][game_id] = {
                            "edge":            t_edge,
                            "prob":            t_prob,
                            "price":           t_price,
                            "kelly_stake":     t_stake,
                            "msg":             t_msg,
                            "tier":            t_tier,
                            "meets_threshold": t_meets_threshold,
                            "matchup":         "%s @ %s" % (away_cn, home_cn),
                            "start_time":      c_time_tw.strftime("%m/%d %H:%M"),
                            "bet":             "%s %.1f" % (side_cn, total_line),
                            "book":            book.get("title", "?"),
                            "model_total":     model_total,
                            "market_total":    t_consensus,
                        }

                    # Keyed by game_id + "_total" (not the bare game_id) --
                    # a spread pick and a totals pick for the same game are
                    # two different bets and must not overwrite each other
                    # in the same history dict.
                    if t_in_range and t_edge > 0.12 and is_official_run and g_date == today_s:
                        total_hist_id = game_id + "_total"
                        existing_th = history.get(total_hist_id)
                        if existing_th is None or t_edge > existing_th.get("edge", 0):
                            history[total_hist_id] = {
                                "date":            g_date,
                                "market":          "total",
                                "bet":             "%s %.1f" % (side_cn, total_line),
                                "book":            book.get("title", "?"),
                                "price":           t_price,
                                "prob":            round(t_prob, 4),
                                "edge":            round(t_edge, 4),
                                "kelly_stake":     t_stake,
                                "predicted_total": round(model_total, 2),
                                "result":          existing_th.get("result", "pending") if existing_th else "pending",
                            }

    total_rec, wins, win_rate, profit = calc_performance(history, league="regular")
    regular_history_count = sum(1 for r in history.values() if r.get("league", "regular") == "regular")
    small_sample_note = "（樣本數 < %d 場，統計僅供參考，不代表長期表現）\n" % MIN_HISTORY_SAMPLE
    perf_msg = (
        "\n📊 **歷史績效報告** (僅統計💎頂級)\n"
        "總推薦: %d 場 | 已結算: %d 場\n"
        "勝率: %.1f%% | 損益: %+.1f 元\n"
        "（以每場 Kelly 建議金額計算）\n"
    ) % (regular_history_count, total_rec, win_rate, profit)
    if total_rec < MIN_HISTORY_SAMPLE:
        perf_msg += small_sample_note

    # Model A (spread) and Model C (totals) get scored separately -- a
    # combined win rate can't tell you whether one market is actually
    # working and the other isn't, since a hot run on one masks a cold
    # one on the other.
    spread_perf  = calc_performance(history, league="regular", market="spread")
    spread_calib = calc_calibration(history, league="regular", market="spread")
    totals_perf  = calc_performance(history, league="regular", market="total")
    totals_calib = calc_calibration(history, league="regular", market="total")
    if spread_calib["n"] or totals_calib["n"]:
        perf_msg += "\n📐 **分市場校準**（Brier/LogLoss 越低越好；MAE 是預測與實際的平均誤差）\n"
        perf_msg += format_market_calibration_line("讓分", spread_perf, spread_calib)
        perf_msg += format_market_calibration_line("大小分", totals_perf, totals_calib)

    # The bot already runs daily, so it naturally passes through every
    # Monday and the 1st of every month without needing a separate
    # schedule/workflow -- just check the date on the run that happens to
    # land there.
    if now_tw.weekday() == 0:
        perf_msg += build_period_report(
            history, "📈 **週報**（近 7 天已結算）",
            (now_tw - timedelta(days=7)).strftime("%Y-%m-%d"),
        )
    if now_tw.day == 1:
        perf_msg += build_period_report(
            history, "📈 **月報**（近 30 天已結算）",
            (now_tw - timedelta(days=30)).strftime("%Y-%m-%d"),
        )

    summer_total, summer_wins, summer_win_rate, _ = calc_performance(history, league="summer")
    if summer_total or any(r.get("league") == "summer" for r in history.values()):
        summer_recorded = sum(1 for r in history.values() if r.get("league") == "summer")
        perf_msg += (
            "\n🏖️ **夏季聯賽歷史績效** (Edge ≥ 6%% 推薦，無 Kelly 資金配置)\n"
            "總推薦: %d 場 | 已結算: %d 場 | 勝率: %.1f%%\n"
        ) % (summer_recorded, summer_total, summer_win_rate)
        if summer_total < MIN_HISTORY_SAMPLE:
            perf_msg += small_sample_note

    qualifying_picks = [p for d in daily_picks.values() for p in d.values() if p["meets_threshold"]] + \
                        [p for d in daily_totals.values() for p in d.values() if p["meets_threshold"]]
    total_picks = len(qualifying_picks)
    avg_edge    = (
        sum(p["edge"] for p in qualifying_picks) / total_picks
        if total_picks else 0
    )

    output = "🏀 NBA %s | 更新: %s | 資料: %s | 推薦: %d 場 | 平均Edge: %+.1f%%\n" % (
        VERSION, now_tw.strftime("%m/%d %H:%M"), data_source, total_picks, avg_edge * 100
    )

    if is_official_run:
        output += "📌 正式記錄版本\n"
    else:
        output += "🔧 測試版本（不寫入回測）\n"

    if not daily_picks and not daily_totals:
        output += "\n今日無賽事資料。\n"
    else:
        for date in sorted(set(daily_picks) | set(daily_totals)):
            label = "📅 今日賽事" if date == today_s else ("⏭ 預告 %s" % date)
            output += "\n%s\n" % label
            for p in sorted(daily_picks.get(date, {}).values(), key=lambda x: x["edge"], reverse=True):
                output += p["msg"]
            for p in sorted(daily_totals.get(date, {}).values(), key=lambda x: x["edge"], reverse=True):
                output += p["msg"]
            output += "-" * 30 + "\n"

    parlay = build_parlay(list(daily_picks.get(today_s, {}).values()))
    if parlay:
        output += "\n🔗 **串關推薦**（僅取今日💎頂級，最多 %d 腿，求穩不求大）:\n" % MAX_PARLAY_LEGS
        for leg in parlay["legs"]:
            output += "> %s | %s @ %.2f（勝率 %.1f%%）\n" % (
                leg["matchup"], leg["bet"], leg["price"], leg["prob"]
            )
        output += "> 合計賠率 %.2f | 全中機率 %.1f%%\n> %s\n" % (
            parlay["combined_price"], parlay["combined_prob"], parlay["note"]
        )

    schedule = build_schedule(games, season_games, today_s, now_utc, daily_picks, daily_totals)

    summer_league["performance"] = {
        "total_recommendations": summer_total,
        "wins":                  summer_wins,
        "win_rate":              round(summer_win_rate, 1),
        "min_sample":            MIN_HISTORY_SAMPLE,
    }

    output += perf_msg
    output += format_summer_league_section(summer_league, now_utc)

    if is_official_run:
        save_history(history)
        log.info("History saved (official run, regular top tier + summer edge >= 6%)")
    else:
        log.info("History NOT saved (test run)")

    export_site_data(
        now_tw=now_tw, data_source=data_source, is_official_run=is_official_run,
        daily_picks=daily_picks, today_s=today_s,
        total_rec=total_rec, wins=wins, win_rate=win_rate, profit=profit,
        summer_league=summer_league, history=history,
        schedule=schedule, parlay=parlay,
        power_rankings=build_power_rankings(live_ratings),
        daily_totals=daily_totals,
        elo_rankings=build_elo_rankings(build_elo_ratings(season_games)),
    )

    log.info("Sending to Discord, length: %d", len(output))
    chunked_send(output, WEBHOOK)
    log.info("Done")


if __name__ == "__main__":
    run()
