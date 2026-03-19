import requests
import os
import random
import logging
import json
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("NBA_V98")

ODDS_API_KEY  = os.getenv("ODDS_API_KEY", "")
RAPID_API_KEY = os.getenv("X_RAPIDAPI_KEY", "")
WEBHOOK       = os.getenv("DISCORD_WEBHOOK", "")
GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO   = os.getenv("GITHUB_REPO", "")

SIMS             = 50000
EDGE_THRESHOLD   = 0.05
MODEL_WEIGHT     = 0.40
MARKET_WEIGHT    = 0.60
DYNAMIC_STD_BASE = 13.5
HOME_ADVANTAGE   = 2.8
MAX_SPREAD       = 18.0
MIN_SPREAD       = 1.0
DISCORD_CHAR_LIMIT = 1900
BANKROLL         = 1000.0
KELLY_FRACTION   = 0.25

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


# ── 回測系統: 用 GitHub Gist 儲存紀錄 ──────────────────
def load_history():
    if not GITHUB_TOKEN:
        return {}
    headers = {"Authorization": "token %s" % GITHUB_TOKEN}
    gists = safe_get("https://api.github.com/gists", headers=headers)
    if not gists:
        return {}
    for g in gists:
        if g.get("description") == "nba_bot_history":
            raw_url = list(g["files"].values())[0]["raw_url"]
            data = safe_get(raw_url)
            return data if isinstance(data, dict) else {}
    return {}


def save_history(history):
    if not GITHUB_TOKEN:
        return
    headers = {
        "Authorization": "token %s" % GITHUB_TOKEN,
        "Content-Type": "application/json",
    }
    content = json.dumps(history, ensure_ascii=False, indent=2)
    gists = safe_get("https://api.github.com/gists", headers=headers)
    gist_id = None
    if gists:
        for g in gists:
            if g.get("description") == "nba_bot_history":
                gist_id = g["id"]
                break

    payload = {
        "description": "nba_bot_history",
        "public": False,
        "files": {"history.json": {"content": content}},
    }
    try:
        if gist_id:
            requests.patch(
                "https://api.github.com/gists/%s" % gist_id,
                headers=headers,
                json=payload,
                timeout=10,
            )
        else:
            requests.post(
                "https://api.github.com/gists",
                headers=headers,
                json=payload,
                timeout=10,
            )
        log.info("History saved to Gist")
    except Exception as e:
        log.error("Failed to save history: %s", e)


def calc_performance(history):
    total = win = 0
    profit = 0.0
    for game_id, record in history.items():
        if record.get("result") not in ["win", "loss"]:
            continue
        total += 1
        stake = record.get("kelly_stake", 10.0)
        if record["result"] == "win":
            win += 1
            profit += stake * (record.get("price", 1.9) - 1)
        else:
            profit -= stake
    win_rate = (win / total * 100) if total else 0
    return total, win, win_rate, profit


# ── Kelly 公式 ──────────────────────────────────────────
def kelly_stake(prob, price, bankroll, fraction=KELLY_FRACTION):
    q = 1 - prob
    b = price - 1
    k = (b * prob - q) / b
    k = max(0.0, k) * fraction
    return round(bankroll * k, 1)


# ── 球隊即時數據 ────────────────────────────────────────
def fetch_team_stats():
    headers = {
        "X-RapidAPI-Key":  RAPID_API_KEY,
        "X-RapidAPI-Host": "api-nba-v1.p.rapidapi.com",
    }
    data = safe_get(
        "https://api-nba-v1.p.rapidapi.com/standings",
        headers=headers,
        params={"league": "standard", "season": "2025"},
    )
    if not data or "response" not in data:
        log.warning("Team stats API failed, using fallback ratings")
        return {}

    ratings = {}
    for team_data in data["response"]:
        try:
            full_name = normalize_team(team_data["team"]["name"])
            if not full_name or full_name not in TEAM_CN:
                continue
            win   = int(team_data["win"]["total"])
            loss  = int(team_data["loss"]["total"])
            total = win + loss
            if total == 0:
                continue
            win_pct    = win / total
            last10_w   = int(team_data["win"].get("lastTen", 5))
            form_score = (last10_w - 5) * 0.6
            ratings[full_name] = {
                "off":  round(110.0 + win_pct * 18.0, 1),
                "def":  round(120.0 - win_pct * 14.0, 1),
                "form": round(form_score, 2),
            }
        except (KeyError, TypeError, ValueError):
            continue

    log.info("Live ratings loaded: %d teams", len(ratings))
    return ratings


# ── 傷病報告 ────────────────────────────────────────────
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


# ── 預測模型 ────────────────────────────────────────────
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

    h_net  = (h_stat["off"] - h_stat["def"]) + h_base.get("form", 0.0)
    a_net  = (a_stat["off"] - a_stat["def"]) + a_base.get("form", 0.0)
    margin = (h_net - a_net) / 2 + HOME_ADVANTAGE
    return margin, [p.capitalize() for p in h_missing], [p.capitalize() for p in a_missing]


def predict_total(home, away, live_ratings):
    h_base = live_ratings.get(home, FALLBACK_RATINGS.get(home, DEFAULT_RATING))
    a_base = live_ratings.get(away, FALLBACK_RATINGS.get(away, DEFAULT_RATING))
    total  = (h_base["off"] + a_base["off"]) / 2 * 2 * 0.97
    return round(total, 1)


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


# ── 賠率資料 ────────────────────────────────────────────
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


# ── Discord 推送 ────────────────────────────────────────
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


# ── 主流程 ──────────────────────────────────────────────
def run():
    if not all([ODDS_API_KEY, RAPID_API_KEY, WEBHOOK]):
        log.error("Missing env vars")
        return

    now_tw  = datetime.utcnow() + timedelta(hours=8)
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
            c_time = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        except (KeyError, ValueError):
            continue

        g_date     = c_time.strftime("%Y-%m-%d")
        home       = normalize_team(g.get("home_team", ""))
        away       = normalize_team(g.get("away_team", ""))
        game_id    = "%s@%s_%s" % (away, home, g_date)
        bookmakers = g.get("bookmakers", [])

        daily_picks.setdefault(g_date, {})
        margin, h_missing, a_missing = predict_margin(home, away, injuries, live_ratings)

        # OU 分析
        model_total    = predict_total(home, away, live_ratings)
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
                    if price <= 1.0:
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
                    missing_str   = "缺陣: " + ", ".join(missing) if missing else "陣容完整"
                    consensus_str = "共識線: %+.1f" % consensus

                    msg = (
                        "**[%s] %s @ %s** (%s)\n"
                        "投注: `%s %+.1f` @ **%.2f** (%s)\n"
                        "> %s | %s\n"
                        "> 勝率: %.1f%% | Edge: %+.1f%% | Kelly建議: $%.1f\n"
                        "> %s\n"
                    ) % (
                        tier, away_cn, home_cn,
                        c_time.strftime("%m/%d %H:%M"),
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

                        # 回測: 記錄新推薦（待結果）
                        if game_id not in history:
                            history[game_id] = {
                                "date":        g_date,
                                "bet":         "%s %+.1f" % (TEAM_CN.get(name, name), line),
                                "price":       price,
                                "prob":        round(prob, 4),
                                "edge":        round(edge, 4),
                                "kelly_stake": stake,
                                "result":      "pending",
                            }

    # ── 績效報告 ──────────────────────────────────────────
    total_rec, wins, win_rate, profit = calc_performance(history)
    perf_msg = (
        "\n📊 **歷史績效報告**\n"
        "總推薦: %d 場 | 已結算: %d 場\n"
        "勝率: %.1f%% | 損益: %+.1f 元\n"
        "（以每場 Kelly 建議金額計算）\n"
    ) % (len(history), total_rec, win_rate, profit)

    # ── 組裝輸出 ──────────────────────────────────────────
    total_picks = sum(len(v) for v in daily_picks.values())
    avg_edge    = (
        sum(p["edge"] for d in daily_picks.values() for p in d.values()) / total_picks
        if total_picks else 0
    )

    output = "🏀 NBA V98.0 | 更新: %s | 資料: %s | 推薦: %d 場 | 平均Edge: %+.1f%%\n" % (
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
