import requests
import os
import random
import logging
import json
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("NBA_V100")

ODDS_API_KEY    = os.getenv("ODDS_API_KEY", "")
WEBHOOK         = os.getenv("DISCORD_WEBHOOK", "")
GITHUB_TOKEN    = os.getenv("GH_TOKEN", "")
BALLDONTLIE_KEY = os.getenv("BALLDONTLIE_KEY", "")

SIMS             = 50000
EDGE_THRESHOLD   = 0.05
MODEL_WEIGHT     = 0.40
MARKET_WEIGHT    = 0.60
DYNAMIC_STD_BASE = 13.5
HOME_ADVANTAGE   = 2.8
MAX_SPREAD       = 18.0
MIN_SPREAD       = 1.0
MIN_PRICE        = 1.0
MAX_PRICE        = 3.0
DISCORD_CHAR_LIMIT = 1900
BANKROLL         = 1000.0
KELLY_FRACTION   = 0.25

IMPACT_PLAYERS = {
    "Los Angeles Lakers":    ["doncic", "james", "reaves"],
    "Washington Wizards":    ["young", "davis", "sarr"],
    "Golden State Warriors": ["curry", "green", "podzemski"],
    "Cleveland Cavaliers":   ["harden", "mitchell", "mobley"],
    "Los Angeles Clippers":  ["leonard", "garland", "powell"],
    "Dallas Mavericks":      ["flagg", "thompson", "jones"],
    "Boston Celtics":        ["brown", "white", "hauser"],
    "Denver Nuggets":        ["jokic", "murray", "gordon"],
    "Oklahoma City Thunder": ["shai", "holmgren", "williams"],
    "San Antonio Spurs":     ["wembanyama", "harper", "cp3"],
    "Milwaukee Bucks":       ["giannis", "lillard", "dieng"],
    "New York Knicks":       ["brunson", "towns", "alvarado"],
    "Houston Rockets":       ["durant", "sengun", "thompson"],
    "Indiana Pacers":        ["siakam", "turner", "zubac"],
}

SEASON_OUT      = {"irving", "haliburton", "butler", "tatum", "vanvleet"}
LIMITED_PLAYERS = {"young", "davis", "curry"}
SUPERSTARS      = {"doncic", "jokic", "shai", "giannis", "durant", "james", "harden", "young", "curry"}
SUPERSTAR_PENALTY = 11.5
STAR_PENALTY      = 8.0
LIMITED_PENALTY   = 5.0

FALLBACK_RATINGS = {
    "Los Angeles Lakers":    {"off": 118.5, "def": 112.0},
    "Boston Celtics":        {"off": 118.0, "def": 111.5},
    "Denver Nuggets":        {"off": 119.0, "def": 111.0},
    "Oklahoma City Thunder": {"off": 119.0, "def": 110.5},
    "Cleveland Cavaliers":   {"off": 117.5, "def": 112.0},
    "Golden State Warriors": {"off": 113.0, "def": 115.5},
    "Milwaukee Bucks":       {"off": 117.0, "def": 113.0},
    "New York Knicks":       {"off": 116.0, "def": 113.0},
    "Houston Rockets":       {"off": 118.0, "def": 112.5},
    "San Antonio Spurs":     {"off": 115.0, "def": 116.0},
    "Dallas Mavericks":      {"off": 113.0, "def": 116.0},
    "Washington Wizards":    {"off": 113.0, "def": 117.0},
    "Los Angeles Clippers":  {"off": 115.5, "def": 114.0},
    "Indiana Pacers":        {"off": 115.0, "def": 114.5},
    "Phoenix Suns":          {"off": 115.0, "def": 115.5},
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


def get_injury_report():
    try:
        from nbainjuries import injury
        now  = datetime.now()
        data = injury.get_reportdata(now)
        if not data:
            raise ValueError("empty response")

        injured = {}
        skip = {"questionable", "probable", "active", "available"}
        for item in data:
            team   = normalize_team(item.get("Team", ""))
            player = item.get("Player Name", "").lower()
            status = item.get("Current Status", "").lower()
            if "," in player:
                last_name = player.split(",")[0].strip()
            else:
                last_name = player.split()[-1] if player.split() else ""
            if not team or not last_name:
                continue
            if any(s in status for s in skip):
                continue
            injured.setdefault(team, []).append(last_name)

        for team, players in IMPACT_PLAYERS.items():
            for p in players:
                if p in SEASON_OUT and p not in injured.get(team, []):
                    injured.setdefault(team, []).append(p)

        log.info("nbainjuries report loaded: %d entries", sum(len(v) for v in injured.values()))
        return injured

    except Exception as e:
        log.warning("nbainjuries failed: %s, using SEASON_OUT fallback", e)
        fallback = {}
        for team, players in IMPACT_PLAYERS.items():
            out = [p for p in players if p in SEASON_OUT]
            if out:
                fallback[team] = out
        return fallback


def fetch_team_stats():
    if not BALLDONTLIE_KEY:
        log.warning("BALLDONTLIE_KEY not set, using fallback")
        return {}
    headers = {"Authorization": BALLDONTLIE_KEY}
    data = safe_get(
        "https://api.balldontlie.io/v1/games",
        headers=headers,
        params={"seasons[]": 2025, "per_page": 100},
    )
    if not data or "data" not in data:
        log.warning("Balldontlie games failed, using fallback")
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
    q = 1 - prob
    b = price - 1
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
        return ["%s(%s)" % (p.capitalize(), "缺" if s == "out" else "限") for p, s in lst]

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
    if not all([ODDS_API_KEY, WEBHOOK]):
        log.error("Missing env vars")
        return

    now_utc = datetime.utcnow()
    now_tw  = now_utc + timedelta(hours=8)
    today_s = now_tw.strftime("%Y-%m-%d")

    live_ratings = fetch_team_stats()
    data_source  = "即時數據" if live_ratings else "靜態備用"
    injuries     = get_injury_report()
    games        = fetch_odds()
    history      = load_history()

    if not games:
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

                    target  = margin if name == home else -margin
                    blended = target * MODEL_WEIGHT + (-consensus) * MARKET_WEIGHT
                    prob    = simulate_cover(blended, line)
                    edge    = prob - (1 / price)

                    if edge < EDGE_THRESHOLD:
                        continue

                    missing = (h_missing if name == home else a_missing) + \
                              (a_missing if name == home else h_missing)

                    stake = kelly_stake(prob, price, BANKROLL)

                    if edge > 0.07:
                        tier = "💎 頂級"
                    elif edge > 0.05:
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
                        }

                    if edge >= EDGE_THRESHOLD:
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
        "\n📊 **歷史績效報告**\n"
        "總推薦: %d 場 | 已結算: %d 場\n"
        "勝率: %.1f%% | 損益: %+.1f 元\n"
        "（以每場 Kelly 建議金額計算）\n"
    ) % (len(history), total_rec, win_rate, profit)

    total_picks = sum(len(v) for v in daily_picks.values())
    avg_edge    = (
        sum(p["edge"] for d in daily_picks.values() for p in d.values()) / total_picks
        if total_picks else 0
    )

    output = "🏀 NBA V100.0 | 更新: %s | 資料: %s | 推薦: %d 場 | 平均Edge: %+.1f%%\n" % (
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

    output += perf_msg

    save_history(history)
    log.info("Sending to Discord, length: %d", len(output))
    chunked_send(output, WEBHOOK)
    log.info("Done")


if __name__ == "__main__":
    run()
