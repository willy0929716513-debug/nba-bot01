import requests
import os
import logging
import json
import time
import re
import math
import sys
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("NBA_V2")

VERSION = "V2.0"

ODDS_API_KEY    = os.getenv("ODDS_API_KEY", "")
WEBHOOK         = os.getenv("DISCORD_WEBHOOK", "")
GITHUB_TOKEN    = os.getenv("GH_TOKEN", "")
BALLDONTLIE_KEY = os.getenv("BALLDONTLIE_KEY", "")

SITE_DATA_PATH = os.getenv("SITE_DATA_PATH", "docs/data/latest.json")

# ntfy.sh (https://ntfy.sh) is a free, no-signup push-notification service --
# POST a message to a topic URL and anyone subscribed (via their app, which
# shows real OS notifications including on a locked phone's lock screen, or
# a browser) gets it. NTFY_TOPIC is secret-ish by nature of the service
# (anyone who knows the topic name can subscribe to or publish on it, there
# being no authentication) -- it must be set as a real repo secret, never
# committed, and is intentionally not given a default value here.
NTFY_BASE_URL = os.getenv("NTFY_BASE_URL", "https://ntfy.sh")
NTFY_TOPIC    = os.getenv("NTFY_TOPIC", "")


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

# Raised from 0.06 -- not fit to this project's own ~30-50-bet history
# (far too few samples to tune a threshold against without just chasing
# noise), but a deliberate, structural selectivity choice: a real,
# consistently exploitable edge against an efficient sportsbook line is
# rare, and the smaller an edge is, the more of it is plausibly just
# estimation error rather than a genuine mispricing. Demanding more edge
# before something counts as a real (Kelly-staked) recommendation trades
# fewer total picks for a higher bar per pick, which is the standard
# selectivity lever in this situation -- not a claim that the
# probability model itself got more accurate.
EDGE_THRESHOLD   = 0.08
MODEL_WEIGHT     = 0.35
MARKET_WEIGHT    = 0.65
# Raised from 13.0 to 15.71 -- not a guess, and not picked to make any
# backtest look more profitable (that would just be fitting noise on a
# small sample). scripts/backtest_margin_calibration.py measured the
# REAL std of predict_margin()'s residuals against 110 out-of-sample
# games this season (ratings computed only from strictly earlier
# games, never the game's own result), independent of which games were
# actually recommended: empirical std 15.71 against this 13.0
# assumption, with only 61.8% of games landing within +/-13 where a
# well-calibrated std would put ~68%. A too-narrow std is exactly what
# makes a probability model overconfident -- this isn't claiming the
# model got more accurate, it's correcting the simulation's own
# acknowledgment of how much it doesn't know, the same role
# EDGE_THRESHOLD's earlier increase played from the opposite direction.
DYNAMIC_STD_BASE = 15.71
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

# The date the totals (大小分) pipeline started blending model_total with
# market consensus before simulating a probability, fixing a real bug where
# it previously ignored market consensus entirely (see blended_total in the
# totals loop below). Settled totals bets dated before this are from the
# old, unblended logic and shouldn't be averaged together with post-fix
# bets when judging whether the fix actually worked -- a few bad weeks
# under a bug that's already fixed shouldn't keep dragging down the number
# that's supposed to say how the CURRENT logic is doing. Used only to
# split reporting into an honest before/after comparison, never to drop
# or hide the pre-fix record itself (still shown, just labeled as before
# the fix rather than silently excluded).
TOTALS_BLEND_FIX_DATE = "2026-11-10"

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

# NHL (ice hockey) -- a second, separate sport pipeline. Names/codes
# confirmed against ESPN's live team list (api as of 2026-11-17), not
# guessed from memory -- notably "Utah Mammoth", the team that relocated
# from Arizona and was later given this permanent name, not "Arizona
# Coyotes" or the interim "Utah Hockey Club".
NHL_TEAM_CN = {
    "Anaheim Ducks": "鴨子", "Boston Bruins": "棕熊", "Buffalo Sabres": "軍刀",
    "Calgary Flames": "火焰", "Carolina Hurricanes": "颶風", "Chicago Blackhawks": "黑鷹",
    "Colorado Avalanche": "雪崩", "Columbus Blue Jackets": "藍衣", "Dallas Stars": "星隊",
    "Detroit Red Wings": "紅翼", "Edmonton Oilers": "油人", "Florida Panthers": "黑豹",
    "Los Angeles Kings": "國王", "Minnesota Wild": "荒野", "Montreal Canadiens": "加拿大人",
    "Nashville Predators": "掠食者", "New Jersey Devils": "惡魔", "New York Islanders": "島人",
    "New York Rangers": "遊騎兵", "Ottawa Senators": "參議員", "Philadelphia Flyers": "飛人",
    "Pittsburgh Penguins": "企鵝", "San Jose Sharks": "鯊魚", "Seattle Kraken": "海妖",
    "St. Louis Blues": "藍調", "Tampa Bay Lightning": "閃電", "Toronto Maple Leafs": "楓葉",
    "Utah Mammoth": "長毛象", "Vancouver Canucks": "加人", "Vegas Golden Knights": "金騎士",
    "Washington Capitals": "首都人", "Winnipeg Jets": "噴射機",
}

# ESPN's team abbreviation, lowercased -- same CDN URL pattern the NBA
# team-logo system already uses (a.espncdn.com/i/teamlogos/<league>/500/
# <code>.png), just under "nhl" instead of "nba".
NHL_TEAM_LOGO_CODE = {
    "Anaheim Ducks": "ana", "Boston Bruins": "bos", "Buffalo Sabres": "buf",
    "Calgary Flames": "cgy", "Carolina Hurricanes": "car", "Chicago Blackhawks": "chi",
    "Colorado Avalanche": "col", "Columbus Blue Jackets": "cbj", "Dallas Stars": "dal",
    "Detroit Red Wings": "det", "Edmonton Oilers": "edm", "Florida Panthers": "fla",
    "Los Angeles Kings": "la", "Minnesota Wild": "min", "Montreal Canadiens": "mtl",
    "Nashville Predators": "nsh", "New Jersey Devils": "nj", "New York Islanders": "nyi",
    "New York Rangers": "nyr", "Ottawa Senators": "ott", "Philadelphia Flyers": "phi",
    "Pittsburgh Penguins": "pit", "San Jose Sharks": "sj", "Seattle Kraken": "sea",
    "St. Louis Blues": "stl", "Tampa Bay Lightning": "tb", "Toronto Maple Leafs": "tor",
    "Utah Mammoth": "utah", "Vancouver Canucks": "van", "Vegas Golden Knights": "vgk",
    "Washington Capitals": "wsh", "Winnipeg Jets": "wpg",
}

# All 32 real NFL teams, verified live against ESPN's teams endpoint.
NFL_TEAM_CN = {
    "Arizona Cardinals": "紅雀", "Atlanta Falcons": "獵鷹", "Baltimore Ravens": "烏鴉",
    "Buffalo Bills": "比爾", "Carolina Panthers": "黑豹", "Chicago Bears": "熊",
    "Cincinnati Bengals": "孟加拉虎", "Cleveland Browns": "布朗", "Dallas Cowboys": "牛仔",
    "Denver Broncos": "野馬", "Detroit Lions": "雄獅", "Green Bay Packers": "包裝工",
    "Houston Texans": "德州人", "Indianapolis Colts": "小馬", "Jacksonville Jaguars": "美洲虎",
    "Kansas City Chiefs": "酋長", "Las Vegas Raiders": "突襲者", "Los Angeles Chargers": "閃電",
    "Los Angeles Rams": "公羊", "Miami Dolphins": "海豚", "Minnesota Vikings": "維京人",
    "New England Patriots": "愛國者", "New Orleans Saints": "聖徒", "New York Giants": "巨人",
    "New York Jets": "噴射機", "Philadelphia Eagles": "老鷹", "Pittsburgh Steelers": "鋼人",
    "San Francisco 49ers": "四九人", "Seattle Seahawks": "海鷹", "Tampa Bay Buccaneers": "海盜",
    "Tennessee Titans": "泰坦", "Washington Commanders": "指揮官",
}

# ESPN's team abbreviation, lowercased -- same CDN URL pattern as NHL's,
# under "nfl" instead of "nhl". Verified live against ESPN's teams endpoint.
NFL_TEAM_LOGO_CODE = {
    "Arizona Cardinals": "ari", "Atlanta Falcons": "atl", "Baltimore Ravens": "bal",
    "Buffalo Bills": "buf", "Carolina Panthers": "car", "Chicago Bears": "chi",
    "Cincinnati Bengals": "cin", "Cleveland Browns": "cle", "Dallas Cowboys": "dal",
    "Denver Broncos": "den", "Detroit Lions": "det", "Green Bay Packers": "gb",
    "Houston Texans": "hou", "Indianapolis Colts": "ind", "Jacksonville Jaguars": "jax",
    "Kansas City Chiefs": "kc", "Las Vegas Raiders": "lv", "Los Angeles Chargers": "lac",
    "Los Angeles Rams": "lar", "Miami Dolphins": "mia", "Minnesota Vikings": "min",
    "New England Patriots": "ne", "New Orleans Saints": "no", "New York Giants": "nyg",
    "New York Jets": "nyj", "Philadelphia Eagles": "phi", "Pittsburgh Steelers": "pit",
    "San Francisco 49ers": "sf", "Seattle Seahawks": "sea", "Tampa Bay Buccaneers": "tb",
    "Tennessee Titans": "ten", "Washington Commanders": "wsh",
}


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


# Same abbreviation problem TEAM_ALIASES documents for the NBA's two LA
# teams also applies to the NHL's: "LA Kings" is not a substring of "Los
# Angeles Kings" either. Without this, a bookmaker posting the shortened
# form would silently fail every `name == home`/`name in (home, away)`
# check in analyze_nhl() (that book's line invisible to all three NHL
# markets) and fail to match in the shared get_consensus_* helpers.
# "utah hockey club" covers the team's prior interim name, in case any
# book's feed lags the "Utah Mammoth" rebrand.
NHL_TEAM_ALIASES = {
    "la kings": "los angeles kings",
    "utah hockey club": "utah mammoth",
}


def normalize_nhl_team(name):
    if not name:
        return name
    n = name.lower()
    n = NHL_TEAM_ALIASES.get(n, n)
    for full in NHL_TEAM_CN:
        if n in full.lower() or full.lower() in n:
            return full
    return name


# The NFL has the same two-LA-teams abbreviation ambiguity as the NBA/NHL
# ("LA Rams"/"LA Chargers" vs. the full "Los Angeles Rams"/"Los Angeles
# Chargers"), plus Las Vegas Raiders sometimes posted as "LV Raiders".
NFL_TEAM_ALIASES = {
    "la rams":      "los angeles rams",
    "la chargers":  "los angeles chargers",
    "lv raiders":   "las vegas raiders",
}


def normalize_nfl_team(name):
    if not name:
        return name
    n = name.lower()
    n = NFL_TEAM_ALIASES.get(n, n)
    for full in NFL_TEAM_CN:
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


def safe_get(url, headers=None, params=None, retries=3, timeout=15, max_wait=None, return_headers=False):
    """return_headers=True additionally returns the response's own
    headers alongside the parsed body, as (json, headers) instead of just
    json -- e.g. so a caller can read The Odds API's x-requests-remaining/
    x-requests-used quota headers, which the plain json body never
    includes. Defaults to False (plain json, as before) for every
    existing caller, including this function's own retry-exhausted
    `return None` path."""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            r.raise_for_status()
            return (r.json(), r.headers) if return_headers else r.json()
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
        # "Doubtful" is one of the NBA's own standard official injury
        # designations (Out / Doubtful / Questionable / Probable /
        # Day-To-Day) -- leaning-out but short of a confirmed "out", the
        # same tier "probable"/"day-to-day" already get folded into here.
        # A real diagnostic run confirmed 8/90 tracked players actually
        # appeared on RotoWire's page that night but only 1 got any
        # status at all, which is consistent with "doubtful" (or other
        # unhandled wording) falling through unclassified rather than
        # the page fetch itself being broken.
        questionable_keywords  = ["questionable", "probable", "day-to-day", "doubtful"]
        clear_keywords         = ["available", "good to go"]

        unclassified = []
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
                elif status is None:
                    idx = text.find(player)
                    snippet = text[max(0, idx - 60):idx + 150] if idx != -1 else ""
                    snippet = " ".join(snippet.split())  # collapse whitespace/newlines/HTML indentation for a one-line log
                    unclassified.append((player, snippet))

        for team, players in IMPACT_PLAYERS.items():
            for p in players:
                if p in SEASON_OUT and p not in out.get(team, []):
                    out.setdefault(team, []).append(p)

        # A low out/questionable count is ambiguous on its own: it could
        # mean most tracked players are genuinely healthy tonight, or it
        # could mean the page's real layout no longer matches these
        # keyword heuristics (RotoWire changing its injury-report page
        # format, for instance) and matches are silently being missed.
        # This distinguishes the two: how many of IMPACT_PLAYERS' tracked
        # last names appear in the fetched text at all, before any
        # status/keyword filtering -- if that number is itself small,
        # the page fetch/content is the thing to look at; if it's large
        # but out/questionable stayed small, the keyword matching is.
        tracked_total = sum(len(v) for v in IMPACT_PLAYERS.values())
        names_present = sum(1 for players in IMPACT_PLAYERS.values() for p in players if p in text)
        log.info(
            "RotoWire injury loaded: %d out, %d questionable (page: %d chars, %d/%d tracked names present in text)",
            sum(len(v) for v in out.values()), sum(len(v) for v in questionable.values()),
            len(text), names_present, tracked_total,
        )
        if unclassified:
            log.info(
                "RotoWire: %d tracked player(s) appear on the page but matched no known status -- "
                "likely wording this scraper doesn't recognize yet: %s",
                len(unclassified), ", ".join(p for p, _ in unclassified),
            )
            # Raw context around each miss -- the actual next step here is
            # reading real page text, not guessing another keyword blind.
            for player, snippet in unclassified:
                log.info("RotoWire unclassified %r context: ...%s...", player, snippet[:200])
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


def fetch_espn_team_ids():
    """ESPN's own numeric team IDs, discovered fresh each run rather than
    hardcoded -- same "discover, don't hardcode" approach already used for
    the Summer League sport_key, so a relocation/renumbering doesn't
    silently go stale. Free, keyless endpoint, same host already used for
    Summer League scores. Returns {normalized_team_name: espn_id}, {} on
    any failure (caller must treat empty as "feature unavailable this
    run", never crash or guess IDs)."""
    data = safe_get(
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams",
        params={"limit": 50},
    )
    if not data:
        return {}
    out = {}
    try:
        leagues = data["sports"][0]["leagues"][0]
        for entry in leagues.get("teams", []):
            team = entry.get("team", {})
            name = normalize_team(team.get("displayName", ""))
            tid  = team.get("id")
            if name and tid:
                out[name] = tid
    except (KeyError, IndexError, TypeError):
        return {}
    return out


ESPN_BOX_STAT_FIELDS = {
    "fieldGoalsAttempted": "fga", "fieldGoalsMade": "fgm",
    "threePointFieldGoalsAttempted": "tpa", "threePointFieldGoalsMade": "tpm",
    "freeThrowsAttempted": "fta", "freeThrowsMade": "ftm",
    "offensiveRebounds": "oreb", "turnovers": "tov", "points": "pts",
    "gamesPlayed": "games",
}


def fetch_team_box_stats(team_ids):
    """Season-to-date cumulative team box-score totals from ESPN's free,
    keyless stats endpoint (same host already used for Summer League
    scores). balldontlie's own equivalent data (box scores / advanced
    stats) requires a paid tier -- see README's 已知限制 history -- this is
    a free source for the same underlying numbers (shot attempts, makes,
    turnovers, rebounds), not the same endpoint.

    Returns {team_name: {fga, fgm, tpa, tpm, fta, ftm, oreb, tov, pts,
    games}}. A team missing from the response, or whose response is
    malformed/incomplete, is simply left out of the result -- never
    filled with a guessed or carried-over value.
    """
    out = {}
    for team_name, tid in team_ids.items():
        data = safe_get(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/%s/statistics" % tid,
        )
        if not data:
            continue
        try:
            categories = data["results"]["stats"]["categories"]
        except (KeyError, TypeError):
            continue
        row = {}
        for cat in categories:
            for stat in cat.get("stats", []):
                key = ESPN_BOX_STAT_FIELDS.get(stat.get("name"))
                if key is None:
                    continue
                try:
                    row[key] = float(stat["value"])
                except (KeyError, TypeError, ValueError):
                    pass
        if len(row) == len(ESPN_BOX_STAT_FIELDS) and row.get("games", 0) > 0 and row.get("fga", 0) > 0:
            out[team_name] = row
    return out


def compute_four_factors(box_stats):
    """Dean Oliver's "four factors" (eFG%, turnover rate, offensive
    rebound rate, free-throw rate) plus an estimated pace (possessions
    per game) and pace-adjusted offensive rating, from real season-to-
    date box totals. Standard textbook formulas applied to real data --
    not fitted to this project's own small bet-outcome sample, so the
    usual small-sample caution that governs calibration/weight tuning in
    this file doesn't apply here the same way; these are fit to this
    season's hundreds of team-games of real box-score totals, same as
    build_live_ratings' SRS.

    oreb_rate here is an offense-only approximation -- own offensive
    rebounds over own missed-shot opportunities (OREB / (FGA - FGM +
    OREB)) -- rather than the textbook-exact OREB / (OREB + opponent
    DREB), since this endpoint doesn't expose opponent-matched rebounding
    splits. Flagged here rather than silently presented as the exact
    textbook version.

    No DRtg here: points allowed would need dividing by the pace of the
    games this team actually played (opponent-paced), which this
    endpoint doesn't give either. build_live_ratings' existing points-
    allowed-per-game approach is left as is rather than faking a
    defensive pace from this team's own offensive one.

    A team with zero games or zero FGA is skipped (new/incomplete data),
    not divided-by-zero into a fabricated number.
    """
    out = {}
    for team, b in box_stats.items():
        if b.get("fga", 0) <= 0 or b.get("games", 0) <= 0:
            continue
        efg = (b["fgm"] + 0.5 * b["tpm"]) / b["fga"]
        ft_rate = b["fta"] / b["fga"]
        tov_denom = b["fga"] + 0.44 * b["fta"] + b["tov"]
        tov_pct = b["tov"] / tov_denom if tov_denom > 0 else None
        oreb_denom = b["fga"] - b["fgm"] + b["oreb"]
        oreb_rate = b["oreb"] / oreb_denom if oreb_denom > 0 else None
        possessions = b["fga"] - b["oreb"] + b["tov"] + 0.44 * b["fta"]
        pace = possessions / b["games"] if b["games"] > 0 else None
        ortg = (b["pts"] / b["games"]) / pace * 100 if pace else None
        out[team] = {
            "efg":       round(efg, 3),
            "ft_rate":   round(ft_rate, 3),
            "tov_pct":   round(tov_pct, 3) if tov_pct is not None else None,
            "oreb_rate": round(oreb_rate, 3) if oreb_rate is not None else None,
            "pace":      round(pace, 1) if pace else None,
            "ortg":      round(ortg, 1) if ortg is not None else None,
        }
    return out


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
        if calib["bias_margin"] is not None:
            line += " ｜ 分差偏差 %+.1f" % calib["bias_margin"]
        if calib["mae_total"] is not None:
            line += " ｜ 總分 MAE %.1f" % calib["mae_total"]
        if calib["bias_total"] is not None:
            line += " ｜ 總分偏差 %+.1f" % calib["bias_total"]
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


def calc_performance(history, league="regular", market=None, since=None):
    """Entries carry a "league" tag ("regular" or "summer") so the two can be
    tracked side by side in one Gist without a regular-season Kelly-based
    profit figure ever getting diluted by summer-league reference-only picks
    that were never actually staked. Untagged entries (written before the
    "league" field existed) default to "regular" for backward compatibility.

    `market` optionally narrows further to "spread" or "total" (untagged
    entries, written before totals bets existed, default to "spread").
    Leaving it None keeps every existing call site's original behavior --
    both markets combined.

    `since` (YYYY-MM-DD) optionally keeps only records with a game date on
    or after it -- for comparing performance before/after a specific
    code change without a stale pre-change sample dragging down (or
    inflating) the read on whether that change actually helped.
    """
    total = win = 0
    profit = 0.0
    for record in history.values():
        if record.get("league", "regular") != league:
            continue
        if market is not None and record.get("market", "spread") != market:
            continue
        if since is not None and record.get("date", "") < since:
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


CALIBRATION_BUCKET_DEFS = [
    ("50-60%", 0.5, 0.6), ("60-70%", 0.6, 0.7), ("70-80%", 0.7, 0.8),
    ("80-90%", 0.8, 0.9), ("90%+",   0.9, 1.01),
]


def calc_calibration(history, league="regular", market=None, since=None):
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

    bias_margin/bias_total are the SIGNED mean of (predicted - actual),
    alongside the existing absolute-error MAE. MAE alone can't tell a
    model that's randomly wrong in both directions apart from one with a
    consistent directional lean (e.g. always overrating the home side, or
    always projecting totals too high) -- the exact asymmetry-shaped bug
    found and fixed in the totals pipeline (model probability never
    blended with market consensus) wouldn't have shown up as a changed
    MAE from that alone, since an unblended estimate isn't necessarily
    biased in one constant direction, just noisier/overconfident. This is
    a second, complementary measurement, still unfitted -- it exists so a
    future directional bias is visible on the dashboard/reports instead
    of requiring the same kind of manual digging that caught this one.

    `since` (YYYY-MM-DD): see calc_performance -- same meaning here.
    """
    outcomes = []
    margin_errors = []
    margin_diffs = []
    total_errors = []
    total_diffs = []
    for record in history.values():
        if record.get("league", "regular") != league:
            continue
        if market is not None and record.get("market", "spread") != market:
            continue
        if since is not None and record.get("date", "") < since:
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
            margin_diffs.append(pm - am)
        pt, at = record.get("predicted_total"), record.get("actual_total")
        if pt is not None and at is not None:
            total_errors.append(abs(pt - at))
            total_diffs.append(pt - at)

    n = len(outcomes)
    if n == 0:
        return {
            "n": 0, "brier": None, "log_loss": None,
            "mae_margin": None, "mae_total": None,
            "bias_margin": None, "bias_total": None,
            "buckets": [],
        }

    brier = sum((p - o) ** 2 for p, o in outcomes) / n

    eps = 1e-6
    log_loss = 0.0
    for p, o in outcomes:
        pc = min(max(p, eps), 1 - eps)
        log_loss += -(o * math.log(pc) + (1 - o) * math.log(1 - pc))
    log_loss /= n

    buckets = []
    for label, lo, hi in CALIBRATION_BUCKET_DEFS:
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
        "bias_margin": round(sum(margin_diffs) / len(margin_diffs), 2) if margin_diffs else None,
        "bias_total":  round(sum(total_diffs) / len(total_diffs), 2) if total_diffs else None,
        "buckets":   buckets,
    }


# Below this real historical sample size, a calibration bucket's actual_rate
# isn't trusted to gate anything -- same "too small to mean anything yet"
# bar as MIN_HISTORY_SAMPLE, just applied per-bucket instead of to the whole
# market. A bucket with 2-3 settled bets can show 0% or 100% purely from
# which way one or two close games bounced.
MIN_CALIBRATED_BUCKET_N = MIN_HISTORY_SAMPLE

# The real-world win-rate band a parlay leg's calibration bucket must fall
# into to count as "stable" -- requested by the person running this bot
# specifically for parlay legs, where one weak leg sinks the whole ticket.
STABLE_WINRATE_LOW  = 0.65
STABLE_WINRATE_HIGH = 0.70


def bucket_actual_rate(calib, prob):
    """What a calibration bucket says ACTUALLY happened for predictions in
    the same probability range as `prob` (0-1 scale) -- not what `prob`
    itself claims. This is the honest way to answer "is a pick like this
    one actually stable," since the model's own stated probability isn't
    reliably true to its number yet (e.g. spread's 60-70% bucket has
    actually been hitting ~42% in real settled history, while the 70-80%
    bucket has done much better) -- using the raw stated prob to judge
    "is this a 65-70%-stable pick" would be answering the wrong question.

    Returns (actual_rate_fraction, n). actual_rate_fraction is None (with
    n=0) when no bucket's range covers this prob at all (shouldn't happen
    for a valid 0-1 prob, but defensive) or that bucket has zero settled
    bets yet -- callers must treat that as "unknown," not as any
    particular rate, and MIN_CALIBRATED_BUCKET_N exists so a technically
    non-zero but tiny n doesn't get treated as reliable either.
    """
    pct = prob * 100
    for label, lo, hi in CALIBRATION_BUCKET_DEFS:
        if lo * 100 <= pct < hi * 100:
            for b in calib.get("buckets", []):
                if b["range"] == label:
                    return b["actual_rate"] / 100, b["n"]
            return None, 0
    return None, 0


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


def get_missing_players(team, injury_data):
    """Which of this team's IMPACT_PLAYERS are out/limited right now, per
    the injury report. Shared by predict_margin and predict_total so a
    missing star affects both the SAME way instead of totals silently
    ignoring injuries entirely (it used to -- predict_total took no
    injury_data param at all)."""
    out_lower = [p.lower() for p in injury_data.get("out", {}).get(team, [])]
    q_lower   = [p.lower() for p in injury_data.get("questionable", {}).get(team, [])]
    result = []
    for k in IMPACT_PLAYERS.get(team, []):
        if k in SEASON_OUT or any(k in p for p in out_lower):
            result.append((k, "out"))
        elif k in LIMITED_PLAYERS or any(k in p for p in q_lower):
            result.append((k, "limited"))
    return result


def injury_adjusted_stat(team, injury_data, live_ratings):
    """This team's off/def/form rating with missing-player penalties
    applied -- the same 60/40 off/def split predict_margin has always
    used, just factored out so predict_total can apply the identical
    adjustment instead of using the unadjusted rating regardless of who's
    actually available to play. Returns (adjusted_stat_dict, missing_list)."""
    base = live_ratings.get(team, FALLBACK_RATINGS.get(team, DEFAULT_RATING))
    stat = dict(base)
    missing = get_missing_players(team, injury_data)
    for p, status in missing:
        penalty = (SUPERSTAR_PENALTY if p in SUPERSTARS else STAR_PENALTY) if status == "out" else LIMITED_PENALTY
        stat["off"] -= penalty * 0.6
        stat["def"] += penalty * 0.4
    return stat, missing


def predict_margin(home, away, injury_data, live_ratings, home_rest=None, away_rest=None, home_adv=None):
    h_base = live_ratings.get(home, FALLBACK_RATINGS.get(home, DEFAULT_RATING))
    a_base = live_ratings.get(away, FALLBACK_RATINGS.get(away, DEFAULT_RATING))
    h_stat, h_missing = injury_adjusted_stat(home, injury_data, live_ratings)
    a_stat, a_missing = injury_adjusted_stat(away, injury_data, live_ratings)

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


def predict_total(home, away, live_ratings, four_factors=None, league_avg_pace=None, injury_data=None):
    """Each side's expected points is its own offense against the
    opponent's defense, not just the two offenses added together -- the
    previous formula ignored defense entirely, so a historically stingy
    defense never pulled a projected total down.

    Also used to silently ignore injuries completely -- a missing star
    changed the model's margin prediction (predict_margin already
    applies the penalty) but never touched the totals estimate at all,
    even though the same injury report was already sitting right there
    in the caller. Reuses injury_adjusted_stat(), the exact same 60/40
    off/def penalty split predict_margin applies, so a missing player
    affects both predictions consistently instead of margin reacting
    and totals not noticing. injury_data=None (the default) preserves
    the old unadjusted behavior exactly, for any caller (e.g. the
    backtest scripts, which have no retroactive injury data available)
    that doesn't pass one.

    When real pace data is available for both teams (four_factors, from
    compute_four_factors()) and a league_avg_pace to compare against, the
    raw off/def-based estimate is scaled by how much faster/slower than
    league average this specific matchup is expected to play. Two slow-
    pace teams should be projected for fewer total points than the raw
    per-game average alone would suggest, and a fast-paced matchup for
    more -- the previous version had no way to account for pace at all.
    Missing pace data for either team (fetch failed, a team too new to
    have a full box-stat line yet, etc.) leaves the estimate exactly as
    it was before this -- no guessed pace is ever substituted in.
    """
    if injury_data is not None:
        h_base, _ = injury_adjusted_stat(home, injury_data, live_ratings)
        a_base, _ = injury_adjusted_stat(away, injury_data, live_ratings)
    else:
        h_base = live_ratings.get(home, FALLBACK_RATINGS.get(home, DEFAULT_RATING))
        a_base = live_ratings.get(away, FALLBACK_RATINGS.get(away, DEFAULT_RATING))
    home_pts = (h_base["off"] + a_base["def"]) / 2
    away_pts = (a_base["off"] + h_base["def"]) / 2
    total = home_pts + away_pts

    if four_factors and league_avg_pace:
        h_ff = four_factors.get(home)
        a_ff = four_factors.get(away)
        if h_ff and a_ff and h_ff.get("pace") and a_ff.get("pace"):
            game_pace = (h_ff["pace"] + a_ff["pace"]) / 2
            total *= game_pace / league_avg_pace

    return round(total, 1)


def get_consensus_moneyline_prob(bookmakers, team_name, normalize=normalize_team):
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
                name  = normalize(outcome.get("name", ""))
                price = outcome.get("price")
                if price and price > 0:
                    raw[name] = 1.0 / price
            total = sum(raw.values())
            if total > 0 and team_name in raw:
                probs.append(raw[team_name] / total)
    return (sum(probs) / len(probs)) if probs else None


def get_consensus_line(bookmakers, team_name, exclude_book=None, normalize=normalize_team):
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
                if normalize(outcome.get("name", "")) == team_name:
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
    """P(blended + N(0, std) + line > 0), via the closed-form normal CDF.

    This used to estimate the same quantity with a 50,000-sample Monte
    Carlo loop (random.gauss() draws counted against zero). The model has
    always just been "is blended+line enough standard deviations from
    zero under a normal distribution" -- there's an exact formula for
    that, so the loop was spending 50,000 random draws per call (across
    every spread/total/NHL moneyline/puck-line/totals price this file
    computes, many times a run) to approximate a number that can be
    computed exactly and faster. This also removes that estimate's own
    sampling noise (~0.4% std error at 50,000 samples), so calling this
    twice with the same inputs now always returns the same probability.
    """
    if std <= 0:
        return 1.0 if (blended + line) > 0 else 0.0
    return 0.5 * (1 + math.erf((blended + line) / (std * math.sqrt(2))))


def log_odds_api_quota(headers, label):
    """The Odds API bills by requests, not by sport -- adding NHL's
    spreads/totals markets on top of NBA's own call means each run now
    spends more of the same shared monthly quota than before. There's no
    way to read the account's remaining quota without making a real
    call, but every real call's response headers already carry it --
    this just surfaces x-requests-remaining/x-requests-used (when
    present; not guaranteed by their API, so this degrades silently if
    missing) into the run's own logs instead of that information going
    unused."""
    if not headers:
        return
    remaining = headers.get("x-requests-remaining")
    used      = headers.get("x-requests-used")
    if remaining is not None or used is not None:
        log.info("Odds API quota (%s): used=%s remaining=%s", label, used, remaining)


def fetch_odds():
    params = {
        "apiKey":     ODDS_API_KEY,
        "regions":    "us",
        "markets":    "spreads,totals",
        "oddsFormat": "decimal",
    }
    result = safe_get(
        "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/",
        params=params, return_headers=True,
    )
    if result is None:
        log.error("Odds API failed")
        return []
    data, headers = result
    log_odds_api_quota(headers, "NBA")
    log.info("Odds loaded: %d games", len(data))
    return data


def fetch_nhl_odds():
    """Same the-odds-api pattern as fetch_odds(), for NHL -- "icehockey_nhl"
    is a stable, documented sport_key (unlike Summer League's, which has
    to be discovered at runtime). Pulls moneyline (h2h), puck line
    (spreads) and totals together -- puck line/totals edges are computed
    against build_nhl_scoring_model()'s own real goal-based numbers, not
    a reuse of the basketball margin/point-total Gaussian simulation."""
    result = safe_get(
        "https://api.the-odds-api.com/v4/sports/icehockey_nhl/odds/",
        params={"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"},
        return_headers=True,
    )
    if result is None:
        log.error("NHL Odds API failed")
        return []
    data, headers = result
    log_odds_api_quota(headers, "NHL")
    log.info("NHL odds loaded: %d games", len(data))
    return data


NHL_SEASON_LOOKBACK_START = "2026-09-20"  # safely before any real season start
NHL_GAMES_CACHE_GIST_DESC = "nba_bot_nhl_games_cache"
# Dates within this many days of today are NEVER trusted from cache, always
# re-fetched fresh -- guards against caching a day as "done" before a late
# game on it has actually gone final, and against any Taiwan/US date-
# boundary mismatch, the same class of bug the NBA side's target_date fix
# (compute_target_date) exists to prevent. A handful of extra ESPN calls
# per run is a trivial cost next to getting that wrong.
NHL_CACHE_RESCAN_DAYS = 3


def load_nhl_games_cache():
    """Cached completed NHL games plus the date through which that cache is
    trusted, so fetch_nhl_season_games() only has to ask ESPN about recent/
    new dates each run instead of re-scanning the entire season from
    scratch every time -- confirmed live to otherwise cost ~25 real seconds
    and ~60 HTTP calls per run even this early in the season, a cost that
    only grows as the season goes on. Same Gist-caching pattern as
    load_live_alert_state(), a separate Gist from both that and the
    settled-bet history Gist."""
    if not GITHUB_TOKEN:
        return None
    headers = {"Authorization": "token %s" % GITHUB_TOKEN}
    gists = safe_get("https://api.github.com/gists", headers=headers, params={"per_page": 100})
    if not gists:
        return None
    for g in gists:
        if g.get("description") == NHL_GAMES_CACHE_GIST_DESC:
            raw_url = list(g["files"].values())[0]["raw_url"]
            data = safe_get(raw_url)
            return data if isinstance(data, dict) else None
    return None


def save_nhl_games_cache(cache):
    if not GITHUB_TOKEN:
        return
    headers = {
        "Authorization": "token %s" % GITHUB_TOKEN,
        "Content-Type":  "application/json",
    }
    content = json.dumps(cache, ensure_ascii=False)
    gists   = safe_get("https://api.github.com/gists", headers=headers, params={"per_page": 100})
    gist_id = None
    if gists:
        for g in gists:
            if g.get("description") == NHL_GAMES_CACHE_GIST_DESC:
                gist_id = g["id"]
                break
    payload = {
        "description": NHL_GAMES_CACHE_GIST_DESC,
        "public":      False,
        "files":       {"nhl_games_cache.json": {"content": content}},
    }
    try:
        if gist_id:
            requests.patch("https://api.github.com/gists/%s" % gist_id, headers=headers, json=payload, timeout=10)
        else:
            requests.post("https://api.github.com/gists", headers=headers, json=payload, timeout=10)
        log.info("NHL games cache saved to Gist (%d games, valid through %s)", len(cache.get("games", [])), cache.get("cached_through"))
    except Exception as e:
        log.error("Failed to save NHL games cache: %s", e)


def fetch_nhl_season_games():
    """Pull this season's real NHL results from ESPN's free scoreboard
    endpoint, one HTTP call per calendar date that isn't already trusted
    from the Gist cache. Unlike balldontlie's paginated whole-season fetch
    for the NBA side, ESPN's NHL scoreboard only accepts a single date per
    call -- a date-range query (?dates=YYYYMMDD-YYYYMMDD) was tried and
    silently returns nothing, confirmed live, not assumed.

    Filters to season.type == 2 (regular season): the real 2026-27 season
    started around Sept 30, but preseason games exist before that and
    would distort early-season Elo if counted, so a cold-start backfill
    scans a bit before any plausible season start and lets the
    season.type field -- not a hand-picked date -- decide what counts.

    See NHL_CACHE_RESCAN_DAYS for why the most recent few days are always
    re-fetched rather than trusted from cache.
    """
    cache = load_nhl_games_cache() or {}
    cached_games = cache.get("games", [])
    cached_through_str = cache.get("cached_through")

    today = (datetime.utcnow() + timedelta(hours=8)).date()
    rescan_boundary = today - timedelta(days=NHL_CACHE_RESCAN_DAYS)

    if cached_through_str:
        cached_through = datetime.strptime(cached_through_str, "%Y-%m-%d").date()
    else:
        cached_through = datetime.strptime(NHL_SEASON_LOOKBACK_START, "%Y-%m-%d").date() - timedelta(days=1)

    trusted_games = [g for g in cached_games if g["date"] <= cached_through.strftime("%Y-%m-%d")]
    fetch_start = cached_through + timedelta(days=1)

    fetched = []
    d = fetch_start
    while d <= today:
        data = safe_get(
            "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
            params={"dates": d.strftime("%Y%m%d")},
            retries=2, max_wait=5,
        )
        if data:
            for ev in data.get("events", []):
                if (ev.get("season") or {}).get("type") != 2:
                    continue
                comp = (ev.get("competitions") or [{}])[0]
                status = ((comp.get("status") or {}).get("type") or {})
                if not status.get("completed"):
                    continue
                competitors = comp.get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), None)
                away = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if not home or not away:
                    continue
                try:
                    hs = int(home.get("score"))
                    vs = int(away.get("score"))
                except (TypeError, ValueError):
                    continue
                fetched.append({
                    "home": (home.get("team") or {}).get("displayName", ""),
                    "away": (away.get("team") or {}).get("displayName", ""),
                    "hs": hs, "vs": vs,
                    "date": d.strftime("%Y-%m-%d"),
                })
        d += timedelta(days=1)

    all_games = trusted_games + fetched
    new_cached_through = max(cached_through, min(rescan_boundary, today))
    save_nhl_games_cache({"cached_through": new_cached_through.strftime("%Y-%m-%d"), "games": all_games})
    log.info(
        "NHL season games: %d trusted (cached) + %d freshly fetched = %d total",
        len(trusted_games), len(fetched), len(all_games),
    )
    return all_games


def fetch_nhl_injuries():
    """Real-time NHL injury report from ESPN's free injuries endpoint.
    Unlike the NBA side's RotoWire text-scrape (get_injury_report()),
    which needs a hardcoded IMPACT_PLAYERS whitelist per team to know
    which player names to even look for in free text, this endpoint
    already returns every team's actual reported injuries directly as
    structured data -- no scraping, no pre-curated star list needed.

    Deliberately informational only: shown in the NHL Discord/website
    output (like the NBA totals pipeline's "missing" field), but NOT fed
    into predict_nhl_score()/analyze_nhl()'s edge math the way the NBA
    side's SUPERSTAR_PENALTY/STAR_PENALTY/LIMITED_PENALTY numbers are.
    Those were tuned against years of this project's own settled NBA
    history; there's no equivalent real calibration for what a given NHL
    injury is worth in goals, and inventing a number would be exactly
    the kind of fabricated figure this project avoids everywhere else.
    """
    data = safe_get("https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries")
    if data is None:
        log.warning("NHL injuries fetch failed")
        return {}

    out = {}
    for team_entry in data.get("injuries", []):
        team = team_entry.get("displayName", "")
        if team not in NHL_TEAM_CN:
            continue
        names = []
        for inj in team_entry.get("injuries", []):
            athlete = inj.get("athlete", {}).get("displayName")
            if not athlete:
                continue
            status = inj.get("status", "")
            tag = "缺" if status in ("Out", "Injured Reserve") else "待定"
            names.append("%s(%s)" % (athlete, tag))
        if names:
            out[team] = names

    log.info("NHL injuries loaded: %d teams with at least one listed injury", len(out))
    return out


def fetch_nfl_odds():
    """Same the-odds-api pattern as fetch_nhl_odds(), for NFL --
    "americanfootball_nfl" is a stable, documented sport_key. Pulls
    moneyline (h2h), spread and totals together, the same three markets
    the NHL pipeline covers."""
    result = safe_get(
        "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/",
        params={"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"},
        return_headers=True,
    )
    if result is None:
        log.error("NFL Odds API failed")
        return []
    data, headers = result
    log_odds_api_quota(headers, "NFL")
    log.info("NFL odds loaded: %d games", len(data))
    return data


NFL_SEASON_LOOKBACK_START = "2026-09-01"  # safely before any real season start
NFL_GAMES_CACHE_GIST_DESC = "nba_bot_nfl_games_cache"
NFL_CACHE_RESCAN_DAYS = 3  # same reasoning as NHL_CACHE_RESCAN_DAYS


def load_nfl_games_cache():
    """Same Gist-caching pattern as load_nhl_games_cache(), a separate Gist
    keyed by its own description so the two sports' caches never collide."""
    if not GITHUB_TOKEN:
        return None
    headers = {"Authorization": "token %s" % GITHUB_TOKEN}
    gists = safe_get("https://api.github.com/gists", headers=headers, params={"per_page": 100})
    if not gists:
        return None
    for g in gists:
        if g.get("description") == NFL_GAMES_CACHE_GIST_DESC:
            raw_url = list(g["files"].values())[0]["raw_url"]
            data = safe_get(raw_url)
            return data if isinstance(data, dict) else None
    return None


def save_nfl_games_cache(cache):
    if not GITHUB_TOKEN:
        return
    headers = {
        "Authorization": "token %s" % GITHUB_TOKEN,
        "Content-Type":  "application/json",
    }
    content = json.dumps(cache, ensure_ascii=False)
    gists   = safe_get("https://api.github.com/gists", headers=headers, params={"per_page": 100})
    gist_id = None
    if gists:
        for g in gists:
            if g.get("description") == NFL_GAMES_CACHE_GIST_DESC:
                gist_id = g["id"]
                break
    payload = {
        "description": NFL_GAMES_CACHE_GIST_DESC,
        "public":      False,
        "files":       {"nfl_games_cache.json": {"content": content}},
    }
    try:
        if gist_id:
            requests.patch("https://api.github.com/gists/%s" % gist_id, headers=headers, json=payload, timeout=10)
        else:
            requests.post("https://api.github.com/gists", headers=headers, json=payload, timeout=10)
        log.info("NFL games cache saved to Gist (%d games, valid through %s)", len(cache.get("games", [])), cache.get("cached_through"))
    except Exception as e:
        log.error("Failed to save NFL games cache: %s", e)


def fetch_nfl_season_games():
    """Pull this season's real NFL results from ESPN's free scoreboard
    endpoint, same incremental-cache pattern as fetch_nhl_season_games().
    Filters to season.type == 2 (regular season), excluding preseason."""
    cache = load_nfl_games_cache() or {}
    cached_games = cache.get("games", [])
    cached_through_str = cache.get("cached_through")

    today = (datetime.utcnow() + timedelta(hours=8)).date()
    rescan_boundary = today - timedelta(days=NFL_CACHE_RESCAN_DAYS)

    if cached_through_str:
        cached_through = datetime.strptime(cached_through_str, "%Y-%m-%d").date()
    else:
        cached_through = datetime.strptime(NFL_SEASON_LOOKBACK_START, "%Y-%m-%d").date() - timedelta(days=1)

    trusted_games = [g for g in cached_games if g["date"] <= cached_through.strftime("%Y-%m-%d")]
    fetch_start = cached_through + timedelta(days=1)

    fetched = []
    d = fetch_start
    while d <= today:
        data = safe_get(
            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
            params={"dates": d.strftime("%Y%m%d")},
            retries=2, max_wait=5,
        )
        if data:
            for ev in data.get("events", []):
                if (ev.get("season") or {}).get("type") != 2:
                    continue
                comp = (ev.get("competitions") or [{}])[0]
                status = ((comp.get("status") or {}).get("type") or {})
                if not status.get("completed"):
                    continue
                competitors = comp.get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), None)
                away = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if not home or not away:
                    continue
                try:
                    hs = int(home.get("score"))
                    vs = int(away.get("score"))
                except (TypeError, ValueError):
                    continue
                fetched.append({
                    "home": (home.get("team") or {}).get("displayName", ""),
                    "away": (away.get("team") or {}).get("displayName", ""),
                    "hs": hs, "vs": vs,
                    "date": d.strftime("%Y-%m-%d"),
                })
        d += timedelta(days=1)

    all_games = trusted_games + fetched
    new_cached_through = max(cached_through, min(rescan_boundary, today))
    save_nfl_games_cache({"cached_through": new_cached_through.strftime("%Y-%m-%d"), "games": all_games})
    log.info(
        "NFL season games: %d trusted (cached) + %d freshly fetched = %d total",
        len(trusted_games), len(fetched), len(all_games),
    )
    return all_games


def fetch_nfl_injuries():
    """Real-time NFL injury report from ESPN's free injuries endpoint,
    same structured-data approach as fetch_nhl_injuries() -- no text
    scraping, no hardcoded star-player whitelist. Informational only;
    see fetch_nhl_injuries()'s docstring for why this isn't fed into the
    edge/probability math."""
    data = safe_get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries")
    if data is None:
        log.warning("NFL injuries fetch failed")
        return {}

    out = {}
    for team_entry in data.get("injuries", []):
        team = team_entry.get("displayName", "")
        if team not in NFL_TEAM_CN:
            continue
        names = []
        for inj in team_entry.get("injuries", []):
            athlete = inj.get("athlete", {}).get("displayName")
            if not athlete:
                continue
            status = inj.get("status", "")
            tag = "缺" if status in ("Out", "Injured Reserve") else "待定"
            names.append("%s(%s)" % (athlete, tag))
        if names:
            out[team] = names

    log.info("NFL injuries loaded: %d teams with at least one listed injury", len(out))
    return out


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
            "matchup":     r["matchup"],
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



def compute_target_date(odds_games, today_s):
    """Which Taiwan date actually is "the next slate to analyze" -- not
    always literally today_s: the-odds-api only lists current/upcoming
    games, and a US evening slate converts to *tomorrow's* Taiwan date
    (see REGULAR_SEASON_START's note on the same ET-to-PT-vs-UTC+8 gap).
    On a typical run, today_s itself usually has nothing left to show --
    its games either already tipped off hours earlier in Taiwan time, or
    the real next slate simply lands on tomorrow's Taiwan calendar date.
    Picks whichever date is nearest at or after today_s that actually has
    games listed in odds_games, falling back to today_s itself (an empty
    result) only if nothing at all is upcoming.

    Shared by build_schedule() and run()'s own per-game loop -- both were
    independently solving the exact same "today_s is usually the wrong
    date" problem, and having only one of them actually do it (the loop
    used a bare today_s for a while) meant the other silently filtered
    out every game on most runs.
    """
    dates_present = set()
    for g in odds_games:
        try:
            c_time_utc = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            continue
        dates_present.add((c_time_utc + timedelta(hours=8)).strftime("%Y-%m-%d"))
    candidates = [d for d in dates_present if d >= today_s]
    return min(candidates) if candidates else today_s


def build_schedule(odds_games, balldontlie_games, today_s, now_utc, daily_picks, daily_totals=None):
    """Every regular-season game on the next upcoming slate (compute_target_date),
    not just the ones with a qualifying recommendation -- a full card so the
    dashboard's "賽程" tab answers "what's coming up" by itself. Final scores
    are filled in via find_final_score() once a game's actually finished
    (relevant when this runs from a manual mid-day/evening trigger rather
    than the early-morning scheduled run).
    """
    target_date = compute_target_date(odds_games, today_s)
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


def build_parlay(picks_for_date, spread_calib=None):
    """Combine same-day picks into a single parlay (串關) suggestion, capped
    at MAX_PARLAY_LEGS legs. The explicit design goal here is "least likely
    to blow up", not "biggest possible payout", so this deliberately does
    NOT throw every qualifying pick of the day into one bet -- more legs
    means more chances for exactly one of them to miss and sink the whole
    ticket.

    Leg eligibility: requires both (a) meets_threshold (a genuine positive-
    edge recommendation, not a sub-threshold "觀察中" pick) and (b) real
    calibration-bucket evidence that picks at this leg's own predicted
    probability actually win in the STABLE_WINRATE_LOW-STABLE_WINRATE_HIGH
    range, with enough settled bets in that bucket (MIN_CALIBRATED_BUCKET_N)
    to trust it. This is NOT "only use 💎頂級" (that's just an edge-size
    tier, with no relationship to how accurate the stated probability has
    actually been) and it is NOT "only use picks that say 65-70%" (the
    model's own stated probability isn't reliably true to its number yet --
    real settled history shows its 60-70%-stated bucket actually hitting far
    below 65-70% at times, while a different stated band has done better).
    Using the bucket's REAL historical hit rate is the only honest way to
    answer "is a pick like this one actually stable" without just
    relabeling the same picks and hoping.

    spread_calib is the live calc_calibration(... market="spread") output;
    None (no calibration data yet, or none passed) means no pick can be
    verified as stable yet, so this returns None rather than falling back
    to an unverified tier-based guess.

    Every leg must still hit for a parlay to pay out at all, so even one
    built entirely from individually-confident picks is mechanically less
    safe than any single leg alone -- that's inherent to what a parlay is,
    not something leg selection can avoid, so it's spelled out in the
    output rather than left implied by "most stable."
    """
    if not spread_calib:
        return None

    stable = []
    for p in picks_for_date:
        if not p.get("meets_threshold"):
            continue
        actual_rate, n = bucket_actual_rate(spread_calib, p["prob"])
        if actual_rate is None or n < MIN_CALIBRATED_BUCKET_N:
            continue
        if STABLE_WINRATE_LOW <= actual_rate <= STABLE_WINRATE_HIGH:
            stable.append(p)

    if len(stable) < 2:
        return None
    stable.sort(key=lambda p: p["prob"], reverse=True)
    legs = stable[:MAX_PARLAY_LEGS]

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


def _matchup_from_history_key(game_id, market):
    """Best-effort matchup string reconstructed from a history entry's own
    dict key, for entries written before the "matchup" field existed.

    The team identity was always encoded in the key -- that's literally
    how grade_pending_history()/find_final_score() locate the right final
    score -- it just was never also copied into a separately readable
    field until now. Since the key already has it, old entries don't
    need to stay blank forever: every key format this file has ever used
    for history (`summer_<matchup>_<date>`, `<away>@<home>_<date>`, and
    `<away>@<home>_<date>_total`) is parsed here. Returns None if the key
    doesn't match any known shape.
    """
    try:
        if game_id.startswith("summer_"):
            rest = game_id[len("summer_"):]
            matchup, _, _date = rest.rpartition("_")
            return matchup or None
        base = game_id[:-len("_total")] if market == "total" and game_id.endswith("_total") else game_id
        teams_part, _date = base.rsplit("_", 1)
        away_en, home_en = teams_part.split("@", 1)
        return "%s @ %s" % (TEAM_CN.get(away_en, away_en), TEAM_CN.get(home_en, home_en))
    except ValueError:
        return None


def build_history_list(history, limit=30):
    items = sorted(history.items(), key=lambda kv: kv[1].get("date", ""), reverse=True)
    out = []
    for game_id, h in items[:limit]:
        market  = h.get("market", "spread")
        matchup = h.get("matchup") or _matchup_from_history_key(game_id, market) or ""
        out.append({
            "date":        h.get("date", ""),
            "matchup":     matchup,
            "bet":         h.get("bet", ""),
            "book":        h.get("book", ""),
            "price":       h.get("price"),
            "prob":        round(h.get("prob", 0) * 100, 1),
            "edge":        round(h.get("edge", 0) * 100, 1),
            "kelly_stake": h.get("kelly_stake"),
            "result":      RESULT_ZH.get(h.get("result", "pending"), h.get("result", "pending")),
            "league":      h.get("league", "regular"),
            "market":      market,
            "final_score": h.get("final_score"),
        })
    return out


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


def build_power_rankings(live_ratings, four_factors=None):
    """Rank order for the dashboard's team power-rankings table: net rating
    (off minus def, already SRS opponent-adjusted by build_live_ratings)
    descending. Empty when live_ratings itself is empty (static-fallback
    mode), same as every other feature that depends on real game data.

    four_factors (from compute_four_factors(), real box-stat-derived
    eFG%/turnover rate/offensive rebound rate/FT rate/pace) are attached
    per team when available, display-only -- same "real signal, shown
    before it's validated enough to wire into Kelly-staked picks" status
    as the Elo ratings and moneyline-implied probability already on this
    dashboard. None for a team missing from the four_factors fetch,
    never a guessed number.
    """
    four_factors = four_factors or {}
    rows = []
    for team, r in live_ratings.items():
        ff = four_factors.get(team, {})
        rows.append({
            "team": TEAM_CN.get(team, team),
            "net":  round(r["off"] - r["def"], 1),
            "off":  round(r["off"], 1),
            "def":  round(r["def"], 1),
            "form": round(r.get("form", 0.0), 2),
            "pace":      ff.get("pace"),
            "efg":       round(ff["efg"] * 100, 1) if ff.get("efg") is not None else None,
            "tov_pct":   round(ff["tov_pct"] * 100, 1) if ff.get("tov_pct") is not None else None,
            "oreb_rate": round(ff["oreb_rate"] * 100, 1) if ff.get("oreb_rate") is not None else None,
            "ft_rate":   round(ff["ft_rate"] * 100, 1) if ff.get("ft_rate") is not None else None,
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


def build_nhl_elo_ratings(games):
    """Same classic sequential Elo math as build_elo_ratings() (NBA), over
    NHL games instead. Kept as its own function rather than generalizing
    the NBA one to take both -- they pull from entirely different raw
    data shapes (balldontlie vs ESPN's scoreboard), and this one's input
    is already the flat {home, away, hs, vs, date} shape
    fetch_nhl_season_games() produces, so sharing would mean forcing one
    of them through an adapter for no real benefit."""
    completed = sorted(games, key=lambda g: g["date"])
    ratings = {}
    for g in completed:
        if g["home"] not in NHL_TEAM_CN or g["away"] not in NHL_TEAM_CN:
            continue
        r_home = ratings.setdefault(g["home"], ELO_INITIAL)
        r_away = ratings.setdefault(g["away"], ELO_INITIAL)
        expected_home = 1.0 / (1.0 + 10 ** ((r_away - r_home) / 400.0))
        actual_home   = 1.0 if g["hs"] > g["vs"] else 0.0
        delta = ELO_K * (actual_home - expected_home)
        ratings[g["home"]] = r_home + delta
        ratings[g["away"]] = r_away - delta
    return {team: round(r, 1) for team, r in ratings.items()}


NHL_SCORING_MODEL_MIN_GAMES = 100  # league-wide, roughly 3+ games/team across 32 teams, before team GF/GA averages are trusted


def build_nhl_scoring_model(games):
    """Goal-based scoring model for NHL puck-line/totals edges, built the
    same way build_nhl_elo_ratings() builds Elo -- from this season's real
    completed games only (ESPN scoreboard via fetch_nhl_season_games()).

    This deliberately does NOT reuse the NBA side's DYNAMIC_STD_BASE/
    TOTAL_STD_BASE: those are calibrated for a sport where games land in
    the 90-130 point range, so a point-scale std is meaningless for a
    2-7 goal sport. margin_std/total_std here are instead the real,
    empirical standard deviation of this season's actual (home - away)
    and (home + away) goal counts, in goal units, so simulate_cover()'s
    Monte Carlo simulation runs on the right scale for hockey instead of
    just inheriting basketball's.

    Team goals-for/against are plain season-to-date per-game averages,
    combined multiplicatively against the league average the same way
    simple sports-scoring models commonly do -- no opponent-strength
    adjustment, no injury data (no NHL injury feed wired in yet). A
    first-pass model, not claimed to be more sophisticated than that.

    Returns margin_std/total_std/league_avg as None (and empty ratings)
    until NHL_SCORING_MODEL_MIN_GAMES completed games are available --
    early in a season, per-team averages from a handful of games would
    be noise, not signal, so puck line/totals picks simply don't run
    yet rather than running on unstable numbers.
    """
    completed = [g for g in games if g["home"] in NHL_TEAM_CN and g["away"] in NHL_TEAM_CN]
    if len(completed) < NHL_SCORING_MODEL_MIN_GAMES:
        return {"ratings": {}, "league_avg": None, "home_adv": 0.0,
                "margin_std": None, "total_std": None, "n_games": len(completed)}

    gf, ga, margins, totals = {}, {}, [], []
    for g in completed:
        h, a, hs, vs = g["home"], g["away"], g["hs"], g["vs"]
        gf.setdefault(h, []).append(hs)
        ga.setdefault(h, []).append(vs)
        gf.setdefault(a, []).append(vs)
        ga.setdefault(a, []).append(hs)
        margins.append(hs - vs)
        totals.append(hs + vs)

    def mean(xs):
        return sum(xs) / len(xs)

    def pstd(xs, m):
        return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5

    all_team_goals = [v for vals in gf.values() for v in vals]
    league_avg = mean(all_team_goals)
    home_adv   = mean(margins)
    ratings    = {t: {"gf": mean(gf[t]), "ga": mean(ga[t])} for t in gf}

    return {
        "ratings":    ratings,
        "league_avg": round(league_avg, 3),
        "home_adv":   round(home_adv, 3),
        "margin_std": round(pstd(margins, home_adv), 3),
        "total_std":  round(pstd(totals, league_avg * 2), 3),
        "n_games":    len(completed),
    }


def predict_nhl_score(home, away, scoring_model):
    """Expected goals for each side: own offense vs opponent's defense,
    each expressed as a multiple of league average, the same off-vs-def
    shape predict_total() uses for NBA -- just on a goals scale with a
    real (not NBA-borrowed) home-ice term."""
    league_avg = scoring_model["league_avg"]
    ratings    = scoring_model["ratings"]
    h = ratings.get(home, {"gf": league_avg, "ga": league_avg})
    a = ratings.get(away, {"gf": league_avg, "ga": league_avg})
    home_goals = league_avg * (h["gf"] / league_avg) * (a["ga"] / league_avg) + scoring_model["home_adv"] / 2
    away_goals = league_avg * (a["gf"] / league_avg) * (h["ga"] / league_avg) - scoring_model["home_adv"] / 2
    return home_goals, away_goals


def build_nfl_elo_ratings(games):
    """Same classic sequential Elo math as build_nhl_elo_ratings(), over
    NFL games instead -- used for NFL's moneyline edge."""
    completed = sorted(games, key=lambda g: g["date"])
    ratings = {}
    for g in completed:
        if g["home"] not in NFL_TEAM_CN or g["away"] not in NFL_TEAM_CN:
            continue
        r_home = ratings.setdefault(g["home"], ELO_INITIAL)
        r_away = ratings.setdefault(g["away"], ELO_INITIAL)
        expected_home = 1.0 / (1.0 + 10 ** ((r_away - r_home) / 400.0))
        actual_home   = 1.0 if g["hs"] > g["vs"] else 0.0
        delta = ELO_K * (actual_home - expected_home)
        ratings[g["home"]] = r_home + delta
        ratings[g["away"]] = r_away - delta
    return {team: round(r, 1) for team, r in ratings.items()}


NFL_SCORING_MODEL_MIN_GAMES = 100  # league-wide; by ~week 7-8 of an 18-week, 32-team season this is comfortably reached


def build_nfl_scoring_model(games):
    """Points-based scoring model for NFL spread/totals edges, same shape
    and same reasoning as build_nhl_scoring_model(): real, empirical
    points-for/against per team and the real standard deviation of this
    season's actual (home - away) and (home + away) point counts, from
    this season's real completed games only -- not a reuse of the NBA
    side's DYNAMIC_STD_BASE/TOTAL_STD_BASE (calibrated for a different
    sport's scoring scale) and not a fabricated number either.

    Returns margin_std/total_std/league_avg as None (and empty ratings)
    until NFL_SCORING_MODEL_MIN_GAMES completed games exist league-wide,
    for the same early-season-noise reason build_nhl_scoring_model()
    holds off.
    """
    completed = [g for g in games if g["home"] in NFL_TEAM_CN and g["away"] in NFL_TEAM_CN]
    if len(completed) < NFL_SCORING_MODEL_MIN_GAMES:
        return {"ratings": {}, "league_avg": None, "home_adv": 0.0,
                "margin_std": None, "total_std": None, "n_games": len(completed)}

    pf, pa, margins, totals = {}, {}, [], []
    for g in completed:
        h, a, hs, vs = g["home"], g["away"], g["hs"], g["vs"]
        pf.setdefault(h, []).append(hs)
        pa.setdefault(h, []).append(vs)
        pf.setdefault(a, []).append(vs)
        pa.setdefault(a, []).append(hs)
        margins.append(hs - vs)
        totals.append(hs + vs)

    def mean(xs):
        return sum(xs) / len(xs)

    def pstd(xs, m):
        return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5

    all_team_points = [v for vals in pf.values() for v in vals]
    league_avg = mean(all_team_points)
    home_adv   = mean(margins)
    ratings    = {t: {"pf": mean(pf[t]), "pa": mean(pa[t])} for t in pf}

    return {
        "ratings":    ratings,
        "league_avg": round(league_avg, 3),
        "home_adv":   round(home_adv, 3),
        "margin_std": round(pstd(margins, home_adv), 3),
        "total_std":  round(pstd(totals, league_avg * 2), 3),
        "n_games":    len(completed),
    }


def predict_nfl_score(home, away, scoring_model):
    """Same off-vs-def shape as predict_nhl_score(), on a points scale."""
    league_avg = scoring_model["league_avg"]
    ratings    = scoring_model["ratings"]
    h = ratings.get(home, {"pf": league_avg, "pa": league_avg})
    a = ratings.get(away, {"pf": league_avg, "pa": league_avg})
    home_pts = league_avg * (h["pf"] / league_avg) * (a["pa"] / league_avg) + scoring_model["home_adv"] / 2
    away_pts = league_avg * (a["pf"] / league_avg) * (h["pa"] / league_avg) - scoring_model["home_adv"] / 2
    return home_pts, away_pts


def tier_from_edge(meets_threshold, edge):
    """Sport-agnostic tier label from an edge value -- same tier cuts
    (0.09/0.12) the NBA pipeline itself uses, shared by analyze_nhl() and
    analyze_nfl() since this logic never depended on anything NHL- or
    NBA-specific to begin with."""
    if not meets_threshold:
        return "👀 觀察中"
    if edge > 0.12:
        return "💎 頂級"
    if edge > 0.09:
        return "🔥 強力"
    return "⭐ 穩定"


NHL_EDGE_THRESHOLD = 0.08  # a relative edge % (model vs. devigged market), not a scoring-scale number -- same bar as the NBA side, since this one doesn't depend on sport-specific units the way std does; no NHL-specific settled history yet to justify a different one


def analyze_nhl(odds_games, elo_ratings, scoring_model, today_s, nhl_injuries=None):
    """NHL picks for today across all three markets: moneyline (Elo blended
    with devigged market win probability), puck line, and totals (both of
    the latter using build_nhl_scoring_model()'s real goal-based
    projection and empirical std, blended with the market consensus line
    the same MODEL_WEIGHT/MARKET_WEIGHT way the NBA spread/totals
    pipelines already do). Puck line and totals are skipped entirely when
    scoring_model has no usable numbers yet (early season, under
    NHL_SCORING_MODEL_MIN_GAMES) -- moneyline still runs on Elo alone.

    nhl_injuries (from fetch_nhl_injuries(), optional) is shown on every
    pick as "missing" for transparency -- purely informational, the same
    way NBA totals picks show "missing" without it changing model_total
    unless injury_data is separately passed into predict_total(). It
    does NOT feed into any of this function's own edge/probability math;
    see fetch_nhl_injuries()'s docstring for why.

    This is a BRAND NEW pipeline with ZERO settled history of its own.
    Unlike the NBA side (years of real calibration data behind its
    thresholds and weights), there's no way yet to confirm any of this
    actually produces a real edge for hockey. Every pick/tier/edge number
    here is real (not fabricated), computed the same honest way as
    everything else in this file -- but genuinely unvalidated, which is
    why run() does not write these to any history/Gist tracking yet, and
    why the export marks this section accordingly.
    """
    picks = {}
    nhl_injuries = nhl_injuries or {}
    has_scoring_model = scoring_model.get("league_avg") is not None

    for g in odds_games:
        try:
            c_time_utc = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            continue
        c_time_tw = c_time_utc + timedelta(hours=8)
        g_date = c_time_tw.strftime("%Y-%m-%d")
        if g_date != today_s:
            continue

        home = g.get("home_team", "")
        away = g.get("away_team", "")
        if home not in NHL_TEAM_CN or away not in NHL_TEAM_CN:
            continue
        bookmakers = g.get("bookmakers", [])

        r_home = elo_ratings.get(home, ELO_INITIAL)
        r_away = elo_ratings.get(away, ELO_INITIAL)
        elo_prob_home = 1.0 / (1.0 + 10 ** ((r_away - r_home) / 400.0))

        game_id    = "%s@%s_%s" % (away, home, g_date)
        start_time = c_time_tw.strftime("%m/%d %H:%M")
        matchup    = "%s @ %s" % (NHL_TEAM_CN.get(away, away), NHL_TEAM_CN.get(home, home))
        missing    = nhl_injuries.get(home, []) + nhl_injuries.get(away, [])
        missing_str = "狀況: " + ", ".join(missing) if missing else "陣容完整"

        def keep(market_name, edge, entry):
            key = (game_id, market_name)
            existing = picks.get(key)
            if existing is None or edge > existing["edge"]:
                picks[key] = entry

        # ---- Moneyline ----
        for book in bookmakers:
            for market in book.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name  = normalize_nhl_team(outcome.get("name", ""))
                    price = outcome.get("price")
                    if not price or price <= 0 or name not in (home, away):
                        continue

                    elo_prob    = elo_prob_home if name == home else (1 - elo_prob_home)
                    market_prob = get_consensus_moneyline_prob(bookmakers, name, normalize=normalize_nhl_team)
                    if market_prob is None:
                        continue

                    blended_prob    = elo_prob * MODEL_WEIGHT + market_prob * MARKET_WEIGHT
                    edge            = blended_prob - (1 / price)
                    meets_threshold = edge >= NHL_EDGE_THRESHOLD
                    stake           = kelly_stake(blended_prob, price, BANKROLL)

                    keep("moneyline", edge, {
                        "market": "moneyline", "tier": tier_from_edge(meets_threshold, edge),
                        "meets_threshold": meets_threshold,
                        "matchup": matchup, "start_time": start_time,
                        "bet": NHL_TEAM_CN.get(name, name) + " 勝",
                        "price": price, "book": book.get("title", "?"),
                        "prob": blended_prob, "edge": edge, "kelly_stake": stake,
                        "missing": missing_str,
                    })

        if not has_scoring_model:
            continue
        model_home_goals, model_away_goals = predict_nhl_score(home, away, scoring_model)
        model_margin = model_home_goals - model_away_goals
        model_total  = model_home_goals + model_away_goals
        margin_std   = scoring_model["margin_std"]
        total_std    = scoring_model["total_std"]

        # ---- Puck line ----
        for book in bookmakers:
            for market in book.get("markets", []):
                if market.get("key") != "spreads":
                    continue
                for outcome in market.get("outcomes", []):
                    name  = normalize_nhl_team(outcome.get("name", ""))
                    line  = outcome.get("point")
                    price = outcome.get("price")
                    if line is None or not price or price <= 0 or name not in (home, away):
                        continue

                    consensus = get_consensus_line(bookmakers, name, exclude_book=book.get("title"), normalize=normalize_nhl_team)
                    if consensus is None:
                        consensus = line
                    if line - consensus < 0:  # only a book posting a better-than-consensus line is worth pricing
                        continue

                    target  = model_margin if name == home else -model_margin
                    blended = target * MODEL_WEIGHT + (-consensus) * MARKET_WEIGHT
                    prob    = simulate_cover(blended, line, std=margin_std)
                    edge    = prob - (1 / price)
                    meets_threshold = edge >= NHL_EDGE_THRESHOLD
                    stake   = kelly_stake(prob, price, BANKROLL)

                    keep("puckline", edge, {
                        "market": "puckline", "tier": tier_from_edge(meets_threshold, edge),
                        "meets_threshold": meets_threshold,
                        "matchup": matchup, "start_time": start_time,
                        "bet": "%s %+.1f" % (NHL_TEAM_CN.get(name, name), line),
                        "price": price, "book": book.get("title", "?"),
                        "prob": prob, "edge": edge, "kelly_stake": stake,
                        "missing": missing_str,
                    })

        # ---- Totals ----
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
                    price      = outcome.get("price")
                    if total_line is None or not price or price <= 0:
                        continue

                    t_consensus = book_consensus_total if book_consensus_total is not None else total_line
                    total_advantage = (t_consensus - total_line) if side == "over" else (total_line - t_consensus)
                    if total_advantage < 0:
                        continue

                    blended_total = model_total * MODEL_WEIGHT + t_consensus * MARKET_WEIGHT
                    prob_over = simulate_cover(blended_total - total_line, 0, std=total_std)
                    prob = prob_over if side == "over" else (1 - prob_over)
                    edge = prob - (1 / price)
                    meets_threshold = edge >= NHL_EDGE_THRESHOLD
                    stake = kelly_stake(prob, price, BANKROLL)
                    side_cn = "大分 Over" if side == "over" else "小分 Under"

                    keep("totals", edge, {
                        "market": "totals", "tier": tier_from_edge(meets_threshold, edge),
                        "meets_threshold": meets_threshold,
                        "matchup": matchup, "start_time": start_time,
                        "bet": "%s %.1f" % (side_cn, total_line),
                        "price": price, "book": book.get("title", "?"),
                        "prob": prob, "edge": edge, "kelly_stake": stake,
                        "missing": missing_str,
                    })

    out = []
    for p in picks.values():
        p = dict(p)
        p["prob"] = round(p["prob"] * 100, 1)
        p["edge"] = round(p["edge"] * 100, 1)
        out.append(p)
    return out


NHL_MARKET_LABEL = {"moneyline": "獨贏", "puckline": "讓分", "totals": "大小分"}


def format_nhl_section(nhl_picks):
    """Discord text block for today's NHL picks (moneyline, puck line,
    totals), mirroring format_summer_league_section()'s style. Always
    labels this experimental/unvalidated -- see analyze_nhl()'s docstring
    -- rather than presenting it with the same confidence as the NBA
    side's years-of-calibration-backed numbers."""
    if not nhl_picks:
        return ""
    lines = ["\n🏒 **NHL 冰球分析**（新功能，Elo/進球模型+市場勝率混合，目前尚無歷史命中率可參考，僅供觀察）\n"]
    for p in sorted(nhl_picks, key=lambda x: x["edge"], reverse=True):
        tag = "🎯推薦" if p["meets_threshold"] else "👀觀察中"
        label = NHL_MARKET_LABEL.get(p["market"], p["market"])
        lines.append("> [%s %s/%s] %s | 投注 %s @ %.2f (%s) | 勝率 %.1f%% | Edge %+.1f%% | %s" % (
            p["tier"], label, tag, p["matchup"], p["bet"], p["price"], p["book"], p["prob"], p["edge"],
            p.get("missing", "陣容完整"),
        ))
    return "\n".join(lines) + "\n"


NFL_EDGE_THRESHOLD = 0.08  # same relative-edge reasoning as NHL_EDGE_THRESHOLD


def analyze_nfl(odds_games, elo_ratings, scoring_model, today_s, nfl_injuries=None):
    """NFL picks for today across all three markets: moneyline, spread,
    totals -- exactly analyze_nhl()'s architecture (Elo for moneyline,
    build_nfl_scoring_model()'s real points-based projection and
    empirical std for spread/totals, blended with market consensus the
    same MODEL_WEIGHT/MARKET_WEIGHT way), just on NFL's points scale
    instead of NHL's goals scale. Spread/totals are skipped entirely
    when scoring_model has no usable numbers yet (under
    NFL_SCORING_MODEL_MIN_GAMES) -- moneyline still runs on Elo alone.

    nfl_injuries (from fetch_nfl_injuries(), optional) is shown on every
    pick as "missing", purely informational -- see
    fetch_nfl_injuries()/fetch_nhl_injuries()'s docstrings for why this
    doesn't feed into the edge/probability math.

    This is a BRAND NEW pipeline with ZERO settled history of its own,
    same caveat as analyze_nhl(): every number here is real, computed
    the same honest way as everything else in this file, but genuinely
    unvalidated.
    """
    picks = {}
    nfl_injuries = nfl_injuries or {}
    has_scoring_model = scoring_model.get("league_avg") is not None

    for g in odds_games:
        try:
            c_time_utc = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            continue
        c_time_tw = c_time_utc + timedelta(hours=8)
        g_date = c_time_tw.strftime("%Y-%m-%d")
        if g_date != today_s:
            continue

        home = g.get("home_team", "")
        away = g.get("away_team", "")
        if home not in NFL_TEAM_CN or away not in NFL_TEAM_CN:
            continue
        bookmakers = g.get("bookmakers", [])

        r_home = elo_ratings.get(home, ELO_INITIAL)
        r_away = elo_ratings.get(away, ELO_INITIAL)
        elo_prob_home = 1.0 / (1.0 + 10 ** ((r_away - r_home) / 400.0))

        game_id    = "%s@%s_%s" % (away, home, g_date)
        start_time = c_time_tw.strftime("%m/%d %H:%M")
        matchup    = "%s @ %s" % (NFL_TEAM_CN.get(away, away), NFL_TEAM_CN.get(home, home))
        missing    = nfl_injuries.get(home, []) + nfl_injuries.get(away, [])
        missing_str = "狀況: " + ", ".join(missing) if missing else "陣容完整"

        def keep(market_name, edge, entry):
            key = (game_id, market_name)
            existing = picks.get(key)
            if existing is None or edge > existing["edge"]:
                picks[key] = entry

        # ---- Moneyline ----
        for book in bookmakers:
            for market in book.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name  = normalize_nfl_team(outcome.get("name", ""))
                    price = outcome.get("price")
                    if not price or price <= 0 or name not in (home, away):
                        continue

                    elo_prob    = elo_prob_home if name == home else (1 - elo_prob_home)
                    market_prob = get_consensus_moneyline_prob(bookmakers, name, normalize=normalize_nfl_team)
                    if market_prob is None:
                        continue

                    blended_prob    = elo_prob * MODEL_WEIGHT + market_prob * MARKET_WEIGHT
                    edge            = blended_prob - (1 / price)
                    meets_threshold = edge >= NFL_EDGE_THRESHOLD
                    stake           = kelly_stake(blended_prob, price, BANKROLL)

                    keep("moneyline", edge, {
                        "market": "moneyline", "tier": tier_from_edge(meets_threshold, edge),
                        "meets_threshold": meets_threshold,
                        "matchup": matchup, "start_time": start_time,
                        "bet": NFL_TEAM_CN.get(name, name) + " 勝",
                        "price": price, "book": book.get("title", "?"),
                        "prob": blended_prob, "edge": edge, "kelly_stake": stake,
                        "missing": missing_str,
                    })

        if not has_scoring_model:
            continue
        model_home_pts, model_away_pts = predict_nfl_score(home, away, scoring_model)
        model_margin = model_home_pts - model_away_pts
        model_total  = model_home_pts + model_away_pts
        margin_std   = scoring_model["margin_std"]
        total_std    = scoring_model["total_std"]

        # ---- Spread ----
        for book in bookmakers:
            for market in book.get("markets", []):
                if market.get("key") != "spreads":
                    continue
                for outcome in market.get("outcomes", []):
                    name  = normalize_nfl_team(outcome.get("name", ""))
                    line  = outcome.get("point")
                    price = outcome.get("price")
                    if line is None or not price or price <= 0 or name not in (home, away):
                        continue

                    consensus = get_consensus_line(bookmakers, name, exclude_book=book.get("title"), normalize=normalize_nfl_team)
                    if consensus is None:
                        consensus = line
                    if line - consensus < 0:  # only a book posting a better-than-consensus line is worth pricing
                        continue

                    target  = model_margin if name == home else -model_margin
                    blended = target * MODEL_WEIGHT + (-consensus) * MARKET_WEIGHT
                    prob    = simulate_cover(blended, line, std=margin_std)
                    edge    = prob - (1 / price)
                    meets_threshold = edge >= NFL_EDGE_THRESHOLD
                    stake   = kelly_stake(prob, price, BANKROLL)

                    keep("spread", edge, {
                        "market": "spread", "tier": tier_from_edge(meets_threshold, edge),
                        "meets_threshold": meets_threshold,
                        "matchup": matchup, "start_time": start_time,
                        "bet": "%s %+.1f" % (NFL_TEAM_CN.get(name, name), line),
                        "price": price, "book": book.get("title", "?"),
                        "prob": prob, "edge": edge, "kelly_stake": stake,
                        "missing": missing_str,
                    })

        # ---- Totals ----
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
                    price      = outcome.get("price")
                    if total_line is None or not price or price <= 0:
                        continue

                    t_consensus = book_consensus_total if book_consensus_total is not None else total_line
                    total_advantage = (t_consensus - total_line) if side == "over" else (total_line - t_consensus)
                    if total_advantage < 0:
                        continue

                    blended_total = model_total * MODEL_WEIGHT + t_consensus * MARKET_WEIGHT
                    prob_over = simulate_cover(blended_total - total_line, 0, std=total_std)
                    prob = prob_over if side == "over" else (1 - prob_over)
                    edge = prob - (1 / price)
                    meets_threshold = edge >= NFL_EDGE_THRESHOLD
                    stake = kelly_stake(prob, price, BANKROLL)
                    side_cn = "大分 Over" if side == "over" else "小分 Under"

                    keep("totals", edge, {
                        "market": "totals", "tier": tier_from_edge(meets_threshold, edge),
                        "meets_threshold": meets_threshold,
                        "matchup": matchup, "start_time": start_time,
                        "bet": "%s %.1f" % (side_cn, total_line),
                        "price": price, "book": book.get("title", "?"),
                        "prob": prob, "edge": edge, "kelly_stake": stake,
                        "missing": missing_str,
                    })

    out = []
    for p in picks.values():
        p = dict(p)
        p["prob"] = round(p["prob"] * 100, 1)
        p["edge"] = round(p["edge"] * 100, 1)
        out.append(p)
    return out


NFL_MARKET_LABEL = {"moneyline": "獨贏", "spread": "讓分", "totals": "大小分"}


def format_nfl_section(nfl_picks):
    """Discord text block for today's NFL picks, mirroring
    format_nhl_section()'s style exactly -- same experimental/
    unvalidated disclaimer, same reasoning."""
    if not nfl_picks:
        return ""
    lines = ["\n🏈 **NFL 美式足球分析**（新功能，Elo/得分模型+市場勝率混合，目前尚無歷史命中率可參考，僅供觀察）\n"]
    for p in sorted(nfl_picks, key=lambda x: x["edge"], reverse=True):
        tag = "🎯推薦" if p["meets_threshold"] else "👀觀察中"
        label = NFL_MARKET_LABEL.get(p["market"], p["market"])
        lines.append("> [%s %s/%s] %s | 投注 %s @ %.2f (%s) | 勝率 %.1f%% | Edge %+.1f%% | %s" % (
            p["tier"], label, tag, p["matchup"], p["bet"], p["price"], p["book"], p["prob"], p["edge"],
            p.get("missing", "陣容完整"),
        ))
    return "\n".join(lines) + "\n"


NFL_SRS_ITERATIONS = 10  # same convergence-iteration count as the NBA SRS pass (build_live_ratings) -- an algorithm parameter, not scale-dependent
NFL_FORM_GAMES     = 4   # NFL plays 18 games/season (vs. NBA's 82); last 4 is the standard "recent form" window for that schedule length


def build_nfl_power_ratings(games):
    """SRS (Simple Rating System) off/def/form ratings for NFL teams --
    same opponent-adjustment math as build_live_ratings() (NBA), just
    on the flat {home, away, hs, vs, date} shape fetch_nfl_season_games()
    produces. Powers the NFL power-rankings table specifically; kept
    separate from build_nfl_scoring_model()'s pf/pa numbers (which feed
    the actual moneyline/spread/totals edge math), the same way the NBA
    side keeps build_live_ratings() and build_elo_ratings() as two
    independent rating systems rather than forcing one number to serve
    both prediction and display.

    The "form" cap is derived from this season's own real margin std
    (half of one std), not a fixed point number borrowed from the NBA
    side or guessed -- same discipline as build_nfl_scoring_model()'s
    margin_std/total_std.
    """
    team_games = {}
    for g in games:
        if g["home"] not in NFL_TEAM_CN or g["away"] not in NFL_TEAM_CN:
            continue
        team_games.setdefault(g["home"], []).append({"opp": g["away"], "pf": g["hs"], "pa": g["vs"], "date": g["date"]})
        team_games.setdefault(g["away"], []).append({"opp": g["home"], "pf": g["vs"], "pa": g["hs"], "date": g["date"]})

    teams = list(team_games.keys())
    if not teams:
        return {}
    for t in teams:
        team_games[t].sort(key=lambda x: x["date"])

    raw_margin = {t: sum(x["pf"] - x["pa"] for x in team_games[t]) / len(team_games[t]) for t in teams}
    raw_off    = {t: sum(x["pf"] for x in team_games[t]) / len(team_games[t]) for t in teams}
    raw_def    = {t: sum(x["pa"] for x in team_games[t]) / len(team_games[t]) for t in teams}

    net = dict(raw_margin)
    for _ in range(NFL_SRS_ITERATIONS):
        net = {
            t: sum((x["pf"] - x["pa"]) + net.get(x["opp"], 0.0) for x in team_games[t]) / len(team_games[t])
            for t in teams
        }

    all_margins = [x["pf"] - x["pa"] for t in teams for x in team_games[t]]
    mean_margin = sum(all_margins) / len(all_margins)
    margin_std  = (sum((m - mean_margin) ** 2 for m in all_margins) / len(all_margins)) ** 0.5
    form_cap    = margin_std * 0.5

    ratings = {}
    for t in teams:
        sos  = net[t] - raw_margin[t]
        off  = raw_off[t] + sos / 2
        deff = raw_def[t] - sos / 2

        recent = team_games[t][-NFL_FORM_GAMES:]
        recent_margin = sum(x["pf"] - x["pa"] for x in recent) / len(recent)
        form = max(-form_cap, min(form_cap, recent_margin - raw_margin[t]))

        ratings[t] = {"off": round(off, 1), "def": round(deff, 1), "form": round(form, 2)}
    return ratings


def build_nfl_power_rankings(nfl_power_ratings):
    """Same shape as build_power_rankings() (NBA) minus four_factors --
    no equivalent free, structured NFL advanced-stats feed is wired in."""
    rows = []
    for team, r in nfl_power_ratings.items():
        rows.append({
            "team": NFL_TEAM_CN.get(team, team),
            "net":  round(r["off"] - r["def"], 1),
            "off":  round(r["off"], 1),
            "def":  round(r["def"], 1),
            "form": round(r.get("form", 0.0), 2),
        })
    rows.sort(key=lambda x: x["net"], reverse=True)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def find_final_score_flat(games, home_en, away_en, taiwan_date, window_days=2):
    """Like find_final_score() (NBA, balldontlie-shaped), but for the flat
    {home, away, hs, vs, date} shape fetch_nhl_season_games()/
    fetch_nfl_season_games() produce. Matched by team identity within a
    window of taiwan_date -- a wider window than the NBA version's
    (which derives an exact expected offset from tip-off-time physics)
    since NFL kickoff times spread across Thursday/Sunday/Monday with no
    single reliable US-date-to-Taiwan-date offset to derive."""
    try:
        center = datetime.strptime(taiwan_date, "%Y-%m-%d")
    except ValueError:
        return None
    for g in games:
        if {g.get("home"), g.get("away")} != {home_en, away_en}:
            continue
        try:
            g_date = datetime.strptime(g["date"], "%Y-%m-%d")
        except (KeyError, ValueError, TypeError):
            continue
        if abs((g_date - center).days) > window_days:
            continue
        return {"home": g["home"], "away": g["away"], "home_score": g["hs"], "away_score": g["vs"]}
    return None


def build_nfl_schedule(odds_games, nfl_games, today_s, now_utc):
    """Same shape/purpose as build_schedule() (NBA): every game on the
    next upcoming slate (compute_target_date() -- already sport-agnostic,
    shared as-is), with final scores filled in via find_final_score_flat()
    once a game's actually finished."""
    target_date = compute_target_date(odds_games, today_s)
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
        home_en = normalize_nfl_team(g.get("home_team", ""))
        away_en = normalize_nfl_team(g.get("away_team", ""))
        if home_en not in NFL_TEAM_CN or away_en not in NFL_TEAM_CN:
            continue
        game_id = "%s@%s_%s" % (away_en, home_en, g_date)
        if game_id in seen:
            continue
        seen.add(game_id)

        score = find_final_score_flat(nfl_games, home_en, away_en, g_date)
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
            "home":       NFL_TEAM_CN.get(home_en, home_en),
            "away":       NFL_TEAM_CN.get(away_en, away_en),
            "start_time": c_time_tw.strftime("%m/%d %H:%M"),
            "status":     status,
            "home_score": home_score,
            "away_score": away_score,
        })
    out.sort(key=lambda x: x["start_time"])
    return {"date": target_date, "games": out}


def export_site_data(now_tw, data_source, is_official_run, daily_picks,
                      total_rec, wins, win_rate, profit, summer_league, history,
                      schedule, parlay, power_rankings, daily_totals=None, elo_rankings=None,
                      parlay_note=None, nhl_picks=None, nfl_picks=None,
                      nfl_power_rankings=None, nfl_schedule=None):
    """Write a JSON snapshot for the static web dashboard (docs/index.html)."""
    daily_totals = daily_totals or {}
    days = []
    for date in sorted(set(daily_picks) | set(daily_totals)):
        picks  = sorted(daily_picks.get(date, {}).values(), key=lambda x: x["edge"], reverse=True)
        totals = sorted(daily_totals.get(date, {}).values(), key=lambda x: x["edge"], reverse=True)
        days.append({
            "date":  date,
            "label": "今日賽事",
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
                    "missing":          t["missing"],
                }
                for t in totals
            ],
        })

    t2, w2, wr2, pf2 = calc_performance(
        history, league="regular", market="total", since=TOTALS_BLEND_FIX_DATE,
    )
    total_since_fix_block = {
        "since": TOTALS_BLEND_FIX_DATE,
        "performance": {
            "total_recommendations": t2,
            "wins":                  w2,
            "win_rate":              round(wr2, 1),
            "profit":                round(pf2, 1),
        },
        "calibration": calc_calibration(
            history, league="regular", market="total", since=TOTALS_BLEND_FIX_DATE,
        ),
    } if t2 else None

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
            # Totals-only, restricted to bets dated on/after the market-blend
            # bugfix (TOTALS_BLEND_FIX_DATE) -- see that constant's comment,
            # shown alongside the all-time totals numbers above so the
            # dashboard can show both: the full history (for transparency)
            # and a clean read on whether the fix actually changed anything,
            # without waiting on a README footnote to explain why they
            # differ. None (not an empty/zero block) until at least one
            # post-fix totals bet has actually settled.
            "total_since_fix": total_since_fix_block,
        },
        "summer_league":      summer_league,
        "history":            build_history_list(history),
        "performance_trend":  build_performance_trend(history),
        "schedule":           schedule,
        "parlay":             parlay,
        "parlay_note":        parlay_note,
        "power_rankings":     power_rankings,
        "elo_rankings":       elo_rankings or [],
        # Brand new, separate sport -- see analyze_nhl()'s docstring for why
        # this is marked experimental (zero settled history yet to validate
        # Elo+market blended this way actually works for hockey). Not
        # wired into any history/Gist tracking yet, unlike the NBA side.
        "nhl": {
            "experimental": True,
            "picks": nhl_picks or [],
        },
        # Same brand-new/experimental treatment as nhl above.
        "nfl": {
            "experimental":   True,
            "picks":          nfl_picks or [],
            "power_rankings": nfl_power_rankings or [],
            "schedule":       nfl_schedule or {"date": "", "games": []},
        },
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

    # There is no `schedule:` trigger on this workflow any more -- GitHub's
    # own cron scheduling for this repo was confirmed (via real run history)
    # to fire 5-11 hours late every day, so the daily run is instead
    # dispatched externally via workflow_dispatch with official_run=true,
    # forwarded here as FORCE_OFFICIAL_RUN. A plain manual run from the
    # Actions UI (official_run left unchecked) stays a test run, same as
    # before. Falling back to a wall-clock hour check only when neither
    # signal is present -- e.g. running locally.
    github_event = os.getenv("GITHUB_EVENT_NAME", "")
    force_official = os.getenv("FORCE_OFFICIAL_RUN", "").strip().lower() == "true"
    if github_event:
        is_official_run = (github_event == "schedule") or force_official
    else:
        is_official_run = (now_utc.hour == 12)
    log.info(
        "Official run: %s (event: %s, forced: %s, UTC hour: %d)",
        is_official_run, github_event or "n/a", force_official, now_utc.hour,
    )

    season_games = fetch_season_games()
    live_ratings = build_live_ratings(season_games)
    team_dates   = build_rest_days(season_games)
    league_home_adv, team_home_adv = build_home_advantage(season_games)
    data_source  = describe_data_source(live_ratings, season_games)

    # Free (ESPN, no key) team box-stat totals -> real four factors + pace,
    # used to pace-adjust predict_total() and shown on the power-rankings
    # dashboard. Empty on any fetch hiccup -- every caller below already
    # treats that as "feature unavailable this run", never a crash.
    espn_team_ids   = fetch_espn_team_ids()
    team_box_stats  = fetch_team_box_stats(espn_team_ids) if espn_team_ids else {}
    four_factors    = compute_four_factors(team_box_stats) if team_box_stats else {}
    _paces          = [f["pace"] for f in four_factors.values() if f.get("pace")]
    league_avg_pace = (sum(_paces) / len(_paces)) if _paces else None
    injuries     = get_injury_report()
    games        = fetch_odds()
    # See compute_target_date(): the next slate to actually analyze is
    # usually NOT today_s itself (a US evening slate converts to
    # tomorrow's Taiwan date), so this -- not today_s -- is what every
    # per-game filter/lookup below keys off of.
    target_date  = compute_target_date(games, today_s)
    history      = load_history()
    grade_pending_history(history, season_games)
    summer_league = analyze_summer_league(now_utc=now_utc)
    record_summer_history(history, summer_league, is_official_run)

    # NHL is a brand new, separate sport pipeline (see analyze_nhl()'s
    # docstring) -- wrapped so that any failure specific to it (the-odds-api
    # not actually covering icehockey_nhl on this plan, ESPN's scoreboard
    # having an outage, etc.) only drops NHL output for this run, and never
    # takes down the main, years-proven NBA pipeline that this whole
    # function otherwise exists for. Computed before the "nothing to
    # report" guard below so an NBA off-day (no games, no Summer League)
    # doesn't also silently swallow a real NHL slate.
    try:
        nhl_odds      = fetch_nhl_odds()
        nhl_games     = fetch_nhl_season_games()
        nhl_elo       = build_nhl_elo_ratings(nhl_games)
        nhl_scoring   = build_nhl_scoring_model(nhl_games)
        nhl_injuries  = fetch_nhl_injuries()
        nhl_picks     = analyze_nhl(nhl_odds, nhl_elo, nhl_scoring, today_s, nhl_injuries=nhl_injuries)
    except Exception as e:
        log.error("NHL pipeline failed, skipping NHL for this run: %s", e)
        nhl_picks = []

    # Same isolated, best-effort treatment as NHL above, for the same
    # reasons -- an NFL-specific failure (odds API coverage, ESPN outage)
    # never takes down the NBA pipeline.
    try:
        nfl_odds          = fetch_nfl_odds()
        nfl_games         = fetch_nfl_season_games()
        nfl_elo           = build_nfl_elo_ratings(nfl_games)
        nfl_scoring       = build_nfl_scoring_model(nfl_games)
        nfl_injuries      = fetch_nfl_injuries()
        nfl_picks         = analyze_nfl(nfl_odds, nfl_elo, nfl_scoring, today_s, nfl_injuries=nfl_injuries)
        nfl_power_ratings = build_nfl_power_ratings(nfl_games)
        nfl_power_rankings = build_nfl_power_rankings(nfl_power_ratings)
        nfl_schedule      = build_nfl_schedule(nfl_odds, nfl_games, today_s, now_utc)
    except Exception as e:
        log.error("NFL pipeline failed, skipping NFL for this run: %s", e)
        nfl_picks = []
        nfl_power_rankings = []
        nfl_schedule = {"date": today_s, "games": []}

    if not games and not summer_league.get("available") and not nhl_picks and not nfl_picks:
        log.info("No regular-season games, no Summer League, no NHL data, and no NFL data; nothing to report")
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

        g_date = c_time_tw.strftime("%Y-%m-%d")
        if g_date != target_date:
            # Only the next slate's games become picks -- a day further
            # out than that used to show up as a "預告" (preview) entry on
            # the website/Discord output days ahead of when it'd actually
            # be bet on, which was confusing rather than useful: by the
            # time that day arrives, the odds/injuries/model have moved on
            # and it gets recomputed fresh anyway, so showing it early
            # added noise, not signal.
            continue

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

        model_total     = predict_total(home, away, live_ratings, four_factors=four_factors, league_avg_pace=league_avg_pace, injury_data=injuries)
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

                    if meets_threshold and is_official_run and g_date == target_date:
                        existing_h = history.get(game_id)
                        if existing_h is None or edge > existing_h.get("edge", 0):
                            history[game_id] = {
                                "date":             g_date,
                                "market":           "spread",
                                "matchup":          "%s @ %s" % (away_cn, home_cn),
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

                    # Bug fix: unlike the spread pipeline (which blends its own
                    # margin with the market consensus line at MODEL_WEIGHT/
                    # MARKET_WEIGHT before simulating), this was comparing the
                    # model's OWN total prediction straight against the book's
                    # line with no market anchoring at all -- t_consensus was
                    # computed and shown in the message, but never actually fed
                    # into the probability. Real settled totals history bears
                    # this out: the model-only total's empirical error (MAE
                    # ~16 points) implies a true std near 20, well above the
                    # 15 this simulation assumed, which is exactly the shape of
                    # overconfident probabilities -- and totals performance
                    # (50% win rate, negative profit, worse-calibrated than
                    # spreads) matches. Blending toward the market consensus
                    # the same way spreads already do closes that specific gap
                    # using the project's own existing weights, not a new
                    # number picked to fit this sample's results.
                    blended_total = model_total * MODEL_WEIGHT + t_consensus * MARKET_WEIGHT
                    prob_over = simulate_cover(blended_total - total_line, 0, std=TOTAL_STD_BASE)
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
                    # A total depends on BOTH teams' offense/defense (unlike a
                    # spread pick, which is directional per side), so the
                    # relevant missing-player list is both teams' combined --
                    # predict_total() has been factoring these into
                    # model_total since the injury-data fix above, but the
                    # message/export never actually said so, same blind spot
                    # the totals pipeline had before that fix.
                    totals_missing = h_missing + a_missing
                    totals_missing_str = "狀況: " + ", ".join(totals_missing) if totals_missing else "陣容完整"
                    t_msg = (
                        "**[%s] %s @ %s** (%s)\n"
                        "投注: `%s %.1f` @ **%.2f** (%s)\n"
                        "> %s\n"
                        "> 模型預測 %.1f 分 | 共識 %.1f 分\n"
                        "> 機率: %.1f%% | Edge: %+.1f%% | Kelly建議: $%.1f\n"
                    ) % (
                        t_tier, away_cn, home_cn,
                        c_time_tw.strftime("%m/%d %H:%M"),
                        side_cn, total_line, t_price, book.get("title", "?"),
                        totals_missing_str,
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
                            "missing":         totals_missing_str,
                            "model_total":     model_total,
                            "market_total":    t_consensus,
                        }

                    # Keyed by game_id + "_total" (not the bare game_id) --
                    # a spread pick and a totals pick for the same game are
                    # two different bets and must not overwrite each other
                    # in the same history dict.
                    if t_meets_threshold and is_official_run and g_date == target_date:
                        total_hist_id = game_id + "_total"
                        existing_th = history.get(total_hist_id)
                        if existing_th is None or t_edge > existing_th.get("edge", 0):
                            history[total_hist_id] = {
                                "date":            g_date,
                                "market":          "total",
                                "matchup":         "%s @ %s" % (away_cn, home_cn),
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
            label = "📅 今日賽事"
            output += "\n%s\n" % label
            for p in sorted(daily_picks.get(date, {}).values(), key=lambda x: x["edge"], reverse=True):
                output += p["msg"]
            for p in sorted(daily_totals.get(date, {}).values(), key=lambda x: x["edge"], reverse=True):
                output += p["msg"]
            output += "-" * 30 + "\n"

    parlay = build_parlay(list(daily_picks.get(target_date, {}).values()), spread_calib=spread_calib)
    parlay_note = None
    if parlay:
        output += (
            "\n🔗 **串關推薦**（只選歷史校準上，跟這一腿同機率區間的推薦，"
            "實際命中率落在 %d-%d%% 之間的場次，最多 %d 腿，求穩不求大）:\n"
        ) % (STABLE_WINRATE_LOW * 100, STABLE_WINRATE_HIGH * 100, MAX_PARLAY_LEGS)
        for leg in parlay["legs"]:
            output += "> %s | %s @ %.2f（勝率 %.1f%%）\n" % (
                leg["matchup"], leg["bet"], leg["price"], leg["prob"]
            )
        output += "> 合計賠率 %.2f | 全中機率 %.1f%%\n> %s\n" % (
            parlay["combined_price"], parlay["combined_prob"], parlay["note"]
        )
    else:
        parlay_note = (
            "目前沒有任何機率區間同時滿足「已結算樣本數 ≥ %d 場」跟「實際命中率落在 "
            "%d-%d%% 之間」兩個條件，所以今天沒有串關推薦——不是今天比較弱，是寧可不湊、"
            "也不用沒驗證過的區間硬湊兩腿出來。"
        ) % (MIN_CALIBRATED_BUCKET_N, STABLE_WINRATE_LOW * 100, STABLE_WINRATE_HIGH * 100)
        if spread_calib.get("n"):
            output += "\n🔗 **串關推薦**：%s\n" % parlay_note

    schedule = build_schedule(games, season_games, today_s, now_utc, daily_picks, daily_totals)

    summer_league["performance"] = {
        "total_recommendations": summer_total,
        "wins":                  summer_wins,
        "win_rate":              round(summer_win_rate, 1),
        "min_sample":            MIN_HISTORY_SAMPLE,
    }

    output += perf_msg
    output += format_summer_league_section(summer_league, now_utc)
    output += format_nhl_section(nhl_picks)
    output += format_nfl_section(nfl_picks)

    if is_official_run:
        save_history(history)
        log.info("History saved (official run, regular top tier + summer edge >= 6%)")
    else:
        log.info("History NOT saved (test run)")

    export_site_data(
        now_tw=now_tw, data_source=data_source, is_official_run=is_official_run,
        daily_picks=daily_picks,
        total_rec=total_rec, wins=wins, win_rate=win_rate, profit=profit,
        summer_league=summer_league, history=history,
        schedule=schedule, parlay=parlay, parlay_note=parlay_note,
        power_rankings=build_power_rankings(live_ratings, four_factors=four_factors),
        daily_totals=daily_totals,
        elo_rankings=build_elo_rankings(build_elo_ratings(season_games)),
        nhl_picks=nhl_picks,
        nfl_picks=nfl_picks,
        nfl_power_rankings=nfl_power_rankings,
        nfl_schedule=nfl_schedule,
    )

    log.info("Sending to Discord, length: %d", len(output))
    chunked_send(output, WEBHOOK)
    notify_new_recommendations(daily_picks, daily_totals, target_date)
    log.info("Done")


# ============================================================
# Live score push alerts -- a SEPARATE, much-more-frequent workflow
# (.github/workflows/live_score_alerts.yml) from the once-daily run()
# above. Polls ESPN's free scoreboard for games that today's already-
# committed docs/data/latest.json marks as a real (meets_threshold)
# recommendation, and pushes a notification via ntfy.sh (free, no
# signup, delivers as a real OS notification -- including on a locked
# phone -- to anyone subscribed to NTFY_TOPIC) the moment one tips off
# or goes final. This can't live in run() itself: run() only fires once
# a day, which would mean "live" alerts that are really a day late.
# ============================================================

LIVE_ALERT_GIST_DESC = "nba_bot_live_alert_state"


def load_live_alert_state():
    """Which games have already triggered a 'tipped off' / 'final' push
    this run-to-run, so the same event doesn't get pushed again every
    time this polls (every ~10 minutes). A separate Gist from the
    settled-bet history Gist -- unrelated data, and keeping them apart
    means this frequent poller can never race/clobber the once-daily
    run()'s history writes."""
    if not GITHUB_TOKEN:
        return {}
    headers = {"Authorization": "token %s" % GITHUB_TOKEN}
    gists = safe_get("https://api.github.com/gists", headers=headers, params={"per_page": 100})
    if not gists:
        return {}
    for g in gists:
        if g.get("description") == LIVE_ALERT_GIST_DESC:
            raw_url = list(g["files"].values())[0]["raw_url"]
            data = safe_get(raw_url)
            return data if isinstance(data, dict) else {}
    return {}


def save_live_alert_state(state):
    if not GITHUB_TOKEN:
        return
    headers = {
        "Authorization": "token %s" % GITHUB_TOKEN,
        "Content-Type":  "application/json",
    }
    content = json.dumps(state, ensure_ascii=False, indent=2)
    gists   = safe_get("https://api.github.com/gists", headers=headers, params={"per_page": 100})
    gist_id = None
    if gists:
        for g in gists:
            if g.get("description") == LIVE_ALERT_GIST_DESC:
                gist_id = g["id"]
                break
    payload = {
        "description": LIVE_ALERT_GIST_DESC,
        "public":      False,
        "files":       {"live_alert_state.json": {"content": content}},
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
        log.info("Live alert state saved to Gist")
    except Exception as e:
        log.error("Failed to save live alert state: %s", e)


def send_ntfy(title, message, priority=3):
    """Push a notification via ntfy.sh's JSON publish endpoint (not the
    simpler curl -d "msg" -H "Title: ..." form) specifically so Chinese
    text round-trips correctly as UTF-8 -- HTTP headers are not a
    reliable place to put non-ASCII text, but a JSON body is. No-ops
    (logged, not an error) when NTFY_TOPIC isn't configured, since an
    unset topic means this feature was never set up, not that it's
    broken.

    Retries on 429 (rate limit) and 5xx: GitHub Actions runners share a
    pool of IPs across many unrelated repos' workflows, so ntfy.sh's
    public server occasionally rate-limits a request that has nothing to
    do with this bot's own traffic. A short retry absorbs that instead of
    silently dropping the notification."""
    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC not set -- skipping push notification: %s", title)
        return
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(
                NTFY_BASE_URL + "/",
                json={"topic": NTFY_TOPIC, "title": title, "message": message, "priority": priority},
                timeout=10,
            )
            if resp.status_code >= 300:
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
                    log.warning(
                        "ntfy push rejected (HTTP %d, attempt %d/%d), retrying: %s -- %s",
                        resp.status_code, attempt, attempts, title, resp.text[:200],
                    )
                    time.sleep(attempt * 5)
                    continue
                log.error(
                    "ntfy push REJECTED (HTTP %d): %s -- %s",
                    resp.status_code, title, resp.text[:500],
                )
                return
            log.info("ntfy push sent: %s", title)
            return
        except Exception as e:
            if attempt < attempts:
                log.warning("ntfy push error (attempt %d/%d), retrying: %s", attempt, attempts, e)
                time.sleep(attempt * 5)
                continue
            log.error("Failed to send ntfy push: %s", e)


def notify_new_recommendations(daily_picks, daily_totals, target_date):
    """Push an immediate ntfy alert the moment the next slate's REAL
    (meets_threshold) recommendations are generated by this run -- not
    waiting for a game to tip off/go final, which is check_live_alerts()'
    separate job (that one polls every ~10 minutes all day; this one
    fires once, right when run() produces the picks).

    Simply: if there's a real qualifying recommendation for the slate
    being analyzed (target_date -- see compute_target_date(); usually
    NOT the literal current calendar date), this pushes -- no gating by
    time of day or by how the run was triggered (schedule vs. a manual
    run from the website's "手動觸發最新分析" button). An earlier version
    only fired between 20:00-06:00 Taiwan time; that was dropped because
    it was one more way a genuine recommendation could silently fail to
    notify for a reason that had nothing to do with whether the
    recommendation was real.
    """
    qualifying = (
        [p for p in daily_picks.get(target_date, {}).values() if p["meets_threshold"]] +
        [t for t in daily_totals.get(target_date, {}).values() if t["meets_threshold"]]
    )
    if not qualifying:
        return

    qualifying.sort(key=lambda x: x["edge"], reverse=True)
    lines = [
        "%s %s %s @ %.2f（勝率 %.1f%%，Edge %+.1f%%）" % (
            p["tier"], p["matchup"], p["bet"], p["price"], p["prob"] * 100, p["edge"] * 100,
        )
        for p in qualifying
    ]
    send_ntfy(
        "🏀 今日新推薦 x%d" % len(qualifying),
        "\n".join(lines),
    )


def get_recommended_matchups_today(site_data_path=None):
    """Real (meets_threshold) recommendations for today's Taiwan date, read
    straight from the already-committed site data rather than re-deriving
    picks from scratch -- this poller's job is just to watch for a score
    change on games the daily run already decided were worth recommending,
    not to run its own odds/model pass. Returns a set of frozenset(
    {away_en, home_en}) pairs. Empty (not an error) if the file is
    missing, unreadable, or today just has no qualifying picks.
    """
    path = site_data_path or SITE_DATA_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()

    today_s = (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d")
    out = set()
    for day in data.get("regular_season", {}).get("days", []):
        if day.get("date") != today_s:
            continue
        for p in day.get("picks", []) + day.get("totals", []):
            if not p.get("meets_threshold"):
                continue
            parts = str(p.get("matchup", "")).split(" @ ")
            if len(parts) != 2:
                continue
            away_en = TEAM_CN_REVERSE.get(parts[0])
            home_en = TEAM_CN_REVERSE.get(parts[1])
            if away_en and home_en:
                out.add(frozenset((away_en, home_en)))
    return out


def check_live_alerts():
    """One poll: find today's real recommendations, check ESPN's live
    scoreboard for each, and push a notification the first time one tips
    off or goes final. Safe to call every ~10 minutes all day -- on a day
    with no qualifying recommendations, or outside game hours, this does
    a couple of free HTTP calls and exits having sent nothing."""
    if not NTFY_TOPIC:
        log.info("NTFY_TOPIC not set -- live score push alerts are disabled, skipping poll entirely")
        return
    recommended = get_recommended_matchups_today()
    if not recommended:
        log.info("No meets_threshold recommendations for today -- nothing to watch")
        return

    scoreboard = safe_get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard")
    events = (scoreboard or {}).get("events", [])
    if not events:
        log.info("ESPN scoreboard returned no events this poll")
        return

    state   = load_live_alert_state()
    changed = False

    for ev in events:
        comp = (ev.get("competitions") or [{}])[0]
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        home_en = normalize_team((home.get("team") or {}).get("displayName", ""))
        away_en = normalize_team((away.get("team") or {}).get("displayName", ""))
        if frozenset((home_en, away_en)) not in recommended:
            continue

        event_id    = ev.get("id") or (away_en + "@" + home_en)
        status      = ((comp.get("status") or {}).get("type") or {})
        game_state  = status.get("state")  # "pre" / "in" / "post"
        matchup_cn  = "%s @ %s" % (TEAM_CN.get(away_en, away_en), TEAM_CN.get(home_en, home_en))
        prev        = state.get(event_id, {})

        if game_state == "in" and not prev.get("notified_start"):
            send_ntfy(
                "🏀 開賽：%s" % matchup_cn,
                "這場比賽有模型推薦，現在開打了。",
            )
            prev["notified_start"] = True
            state[event_id] = prev
            changed = True

        if game_state == "post" and not prev.get("notified_final"):
            score_line = "%s %s - %s %s" % (
                TEAM_CN.get(away_en, away_en), away.get("score"),
                home.get("score"), TEAM_CN.get(home_en, home_en),
            )
            send_ntfy(
                "🏁 已完賽：%s" % matchup_cn,
                "最終比分 %s" % score_line,
            )
            prev["notified_final"] = True
            state[event_id] = prev
            changed = True

    if changed:
        save_live_alert_state(state)


def run_live_alerts_loop(budget_minutes=340, interval_seconds=180):
    """Keep calling check_live_alerts() inside this one job run instead of
    relying on GitHub Actions to actually start the next scheduled run on
    time. GitHub's '*/10 * * * *' schedule is documented as best-effort,
    and in practice on this repo it has been observed firing hours apart
    rather than every 10 minutes -- a GitHub Actions platform behavior,
    not something a cron expression can fix. By polling repeatedly for
    most of a job's 6-hour hard limit, a single run that does manage to
    start covers nearly all of the gap until the next one, so tip-off/
    final pushes stay close to real-time even when the external
    scheduling isn't."""
    deadline = time.time() + budget_minutes * 60
    poll_count = 0
    while True:
        check_live_alerts()
        poll_count += 1
        if time.time() + interval_seconds >= deadline:
            break
        time.sleep(interval_seconds)
    log.info("Live alerts loop finished after %d polls (~%d min budget)", poll_count, budget_minutes)


def send_test_ntfy():
    """Sends one unconditional push, bypassing the "only on a real
    tip-off/final" logic entirely -- a way to confirm the whole chain
    (NTFY_TOPIC secret wired correctly, the Actions runner can reach
    ntfy.sh, the phone is actually subscribed) works, without needing to
    wait for a live game with a real recommendation to line up."""
    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC not set -- nothing to test. Add the secret first.")
        return
    send_ntfy(
        "🏀 NBA Bot 測試通知",
        "如果手機上看到這則通知，代表 NTFY_TOPIC 設定成功，推播功能可以正常運作。",
    )


if __name__ == "__main__":
    if "--live-alerts-test" in sys.argv:
        send_test_ntfy()
    elif "--live-alerts" in sys.argv:
        run_live_alerts_loop()
    else:
        run()
