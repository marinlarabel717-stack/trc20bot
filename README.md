# trc20bot

Telegram USDT/TRC20 monitor bot.

## Features

- Monitor multiple TRC20 addresses
- Incoming and outgoing USDT transfer alerts
- Address remarks
- Transaction details and TX links
- Balance queries
- Daily and monthly income statistics
- Duplicate prevention

## Quick Start

1. Create a virtual environment and install dependencies.
2. Copy `.env.example` to `.env` and fill in the values.
3. Run `python bot.py`

## Commands

- `/start` view help
- `/addaddr <address> [remark]` add or update a monitored address
- `/deladdr <id|address>` remove a monitored address
- `/listaddr` list monitored addresses
- `/balance [id|address]` query balances
- `/history [day|month|all] [limit]` view recent history
- `/stats [day|month|all]` view income statistics
- `/tx <hash>` query saved transaction details
- `/scan` run a manual scan immediately
