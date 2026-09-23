"""Daily value-bet email: Pinnacle (pinnapi.com) vs Superbet odds, plus the running closing-line record.

Environment:
  PINNAPI_KEY                              pinnapi.com API key (secret)
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD, EMAIL_TO   same as daily_recommendations.py
"""

from __future__ import annotations

import argparse

import pandas as pd

from daily_recommendations import send_email
from superbet.daily import email_html, run_daily, weekly_html
from superbet.staking import StakingConfig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", required=True, help="YYYY-MM-DD (Superbet scrape date)")
    parser.add_argument("--superbet-xlsx", default="output/matches.xlsx")
    parser.add_argument("--state-dir", default="state", help="Where snapshots and the bet log are kept")
    parser.add_argument("--team-map", default="team_map.csv", help="Optional CSV superbet_name,name")
    parser.add_argument("--ev-min", type=float, default=0.02)
    parser.add_argument("--bankroll", type=float, default=1000.0)
    parser.add_argument("--weekly", action="store_true",
                        help="Send the weekly summary for the 7 days before --date instead of the daily report")
    parser.add_argument("--dry-run", action="store_true", help="Print the email instead of sending it")
    args = parser.parse_args()

    if args.weekly:
        subject, body = weekly_html(args.state_dir, args.date)
        print(subject)
        if args.dry_run:
            print(body)
        else:
            send_email(subject, body)
        return

    team_map = None
    try:
        m = pd.read_csv(args.team_map)
        team_map = dict(zip(m["superbet_name"], m["name"]))
    except FileNotFoundError:
        pass
    result = run_daily(args.date, args.superbet_xlsx, args.state_dir, StakingConfig(ev_min=args.ev_min,
                       bankroll=args.bankroll), args.ev_min, team_map=team_map)
    body = email_html(result, args.date)
    subject = f"Value bets ({len(result.bets)}) -- {args.date}"
    print(subject)
    if len(result.unmatched):
        print(f"{len(result.unmatched)} meciuri Superbet fara pereche pe Pinnacle")
    if args.dry_run:
        print(body)
        return
    send_email(subject, body)


if __name__ == "__main__":
    main()
