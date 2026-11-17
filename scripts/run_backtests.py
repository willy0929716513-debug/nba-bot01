"""Run both calibration backtests against one shared fetch of season games.

Running backtest_margin_calibration.py and backtest_total_calibration.py
back to back, each fetching the season independently, hit balldontlie's
rate limit on the second fetch every time (confirmed from a real run --
the first script's fetch succeeds after retries, but the second one in
the same job gets 429'd on all retries since the limit hadn't recovered
yet). Fetching once here and passing the same games list to both avoids
that entirely instead of just adding more retries/waiting.

Run manually: `python scripts/run_backtests.py` (needs BALLDONTLIE_KEY).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nba_bot as nb
import backtest_margin_calibration as margin_bt
import backtest_total_calibration as total_bt


def main():
    games = nb.fetch_season_games()
    if not games:
        print("No games returned (missing/rate-limited BALLDONTLIE_KEY?) -- aborting.")
        return 1

    print("=" * 20, "MARGIN (spread) BACKTEST", "=" * 20)
    margin_result = margin_bt.main(games)

    print()
    print("=" * 20, "TOTAL BACKTEST", "=" * 20)
    total_result = total_bt.main(games)

    return margin_result or total_result or 0


if __name__ == "__main__":
    sys.exit(main())
