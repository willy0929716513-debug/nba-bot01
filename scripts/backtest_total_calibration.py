"""Chronological backtest of predict_total()'s assumed uncertainty.

Companion to backtest_margin_calibration.py, prompted by real recorded
totals performance: 31 settled recommendations, 51.6% win rate, -27.1
profit, and calibration buckets where the 60-70% bucket (n=17) and the
70-80% bucket (n=13) both actually hit at about the same ~53% --
predicted confidence isn't tracking real outcomes at all for totals,
which is a more serious finding than the spread side's (real, but
milder) overconfidence.

Same method as the margin backtest: for each completed game, build
live_ratings from strictly EARLIER completed games only, predict the
game's total, and compare to the real final total. Skips a team's
games until it has at least MIN_PRIOR_GAMES completed.

One real, deliberate simplification versus the live production
formula: predict_total() also applies a pace adjustment from
compute_four_factors(), sourced from CURRENT ESPN box-score stats --
there's no way to ask ESPN for "team pace as of game date X" for a
past game, so this backtest calls predict_total() with
four_factors=None (no pace adjustment), testing only the underlying
off/def-based total estimate. That means this UNDERSTATES whatever the
pace layer currently contributes (for better or worse) -- it answers
"is the off/def total estimate's own uncertainty well-calibrated",
not "is the exact production formula well-calibrated". Worth knowing,
not grounds to throw the result out.

Run manually: `python scripts/backtest_total_calibration.py` (needs
BALLDONTLIE_KEY). Not part of the daily pipeline.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nba_bot as nb

MIN_PRIOR_GAMES = 5


def main():
    games = nb.fetch_season_games()
    if not games:
        print("No games returned (missing/rate-limited BALLDONTLIE_KEY?) -- aborting.")
        return 1

    def g_date(g):
        return str(g.get("date", ""))[:10]

    final_games = [
        g for g in games
        if g.get("status") == "Final" and g.get("home_team_score") and g.get("visitor_team_score")
    ]
    final_games.sort(key=g_date)
    print("Total completed games this season: %d" % len(final_games))

    games_played = {}
    residuals = []
    skipped_cold_start = 0

    for i, g in enumerate(final_games):
        try:
            home = nb.normalize_team(g["home_team"]["full_name"])
            away = nb.normalize_team(g["visitor_team"]["full_name"])
        except (KeyError, TypeError):
            continue

        h_prior = games_played.get(home, 0)
        a_prior = games_played.get(away, 0)

        if h_prior >= MIN_PRIOR_GAMES and a_prior >= MIN_PRIOR_GAMES:
            ratings = nb.build_live_ratings(final_games[:i])
            if home in ratings and away in ratings:
                pred_total = nb.predict_total(home, away, ratings)  # no pace adjustment -- see module docstring
                actual_total = g["home_team_score"] + g["visitor_team_score"]
                residuals.append(pred_total - actual_total)
        else:
            skipped_cold_start += 1

        games_played[home] = h_prior + 1
        games_played[away] = a_prior + 1

    n = len(residuals)
    print("Skipped (cold start, <%d prior games for a team): %d" % (MIN_PRIOR_GAMES, skipped_cold_start))
    print("Evaluated games (out-of-sample, as-of-date ratings, no pace adjustment): %d" % n)
    if n < 20:
        print("Too few evaluated games for a meaningful read -- stopping here.")
        return 0

    mae = sum(abs(r) for r in residuals) / n
    bias = sum(residuals) / n
    variance = sum((r - bias) ** 2 for r in residuals) / n
    std = variance ** 0.5
    within_assumed_std = sum(1 for r in residuals if abs(r) <= nb.TOTAL_STD_BASE) / n

    print("")
    print("MAE (mean absolute error of predicted total): %.2f points" % mae)
    print("Signed bias (mean of predicted-minus-actual):  %+.2f points" % bias)
    print("Empirical std of residuals:                    %.2f points" % std)
    print("Current TOTAL_STD_BASE assumption:             %.1f points" % nb.TOTAL_STD_BASE)
    print(
        "Fraction of games within +/-%.1f (current std): %.1f%% (a well-calibrated std would put ~68%% here)"
        % (nb.TOTAL_STD_BASE, within_assumed_std * 100)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
