"""Chronological backtest of predict_margin()'s assumed uncertainty.

The real calibration data recorded so far (from the 29 settled spread
*recommendations*, i.e. only the subset of games the model was confident
enough to actually bet on) shows a predicted-margin MAE of ~21.3 points
against an assumed simulation std (DYNAMIC_STD_BASE) of only 13, plus a
signed bias of about +4.7 (predicting the recommended side wins by more
than it actually does). That sample is small (n=29) and is not a random
sample of games -- it's only the ones that already looked attractive to
the model, which could itself distort the numbers.

This script checks the same question (is 13 the right std, is there a
real directional bias) against EVERY completed game of the season, not
just recommended bets, using each team's rating as of strictly BEFORE
that game was played -- never the final, end-of-season rating, which
would leak the game's own result into its own prediction. No injury
data is applied retroactively (not available for past games); that's a
known limitation, not an oversight -- it adds noise but shouldn't bias
the result in a particular direction.

Run manually: `python scripts/backtest_margin_calibration.py` (needs
BALLDONTLIE_KEY in the environment). Not part of the daily pipeline.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nba_bot as nb

MIN_PRIOR_GAMES = 5  # per team, before trusting its rating enough to test on


def main(games=None):
    # Accepts a pre-fetched games list (see run_backtests.py) so a combined
    # run doesn't call balldontlie twice back to back and double up on its
    # rate limit -- confirmed to actually happen: a solo run of this script
    # is fine, but running this and backtest_total_calibration.py in the
    # same job sequentially got the second fetch 429'd on every retry.
    if games is None:
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

    empty_injury = {"out": {}, "questionable": {}}
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
            ratings = nb.build_live_ratings(final_games[:i])  # strictly earlier games only
            if home in ratings and away in ratings:
                pred_margin, _, _ = nb.predict_margin(home, away, empty_injury, ratings)
                actual_margin = g["home_team_score"] - g["visitor_team_score"]
                residuals.append(pred_margin - actual_margin)
        else:
            skipped_cold_start += 1

        games_played[home] = h_prior + 1
        games_played[away] = a_prior + 1

    n = len(residuals)
    print("Skipped (cold start, <%d prior games for a team): %d" % (MIN_PRIOR_GAMES, skipped_cold_start))
    print("Evaluated games (out-of-sample, as-of-date ratings): %d" % n)
    if n < 20:
        print("Too few evaluated games for a meaningful read -- stopping here.")
        return 0

    mae = sum(abs(r) for r in residuals) / n
    bias = sum(residuals) / n
    variance = sum((r - bias) ** 2 for r in residuals) / n
    std = variance ** 0.5
    within_assumed_std = sum(1 for r in residuals if abs(r) <= nb.DYNAMIC_STD_BASE) / n

    print("")
    print("MAE (mean absolute error of predicted margin): %.2f points" % mae)
    print("Signed bias (mean of predicted-minus-actual):   %+.2f points" % bias)
    print("Empirical std of residuals:                     %.2f points" % std)
    print("Current DYNAMIC_STD_BASE assumption:            %.1f points" % nb.DYNAMIC_STD_BASE)
    print(
        "Fraction of games within +/-%.1f (current std): %.1f%% (a well-calibrated std would put ~68%% here)"
        % (nb.DYNAMIC_STD_BASE, within_assumed_std * 100)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
