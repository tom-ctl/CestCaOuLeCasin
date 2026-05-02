# Binance Testnet Crypto Trading Bot

Production-oriented starter bot for Binance via CCXT, Telegram confirmations, simple breakout signals, and stop-loss / take-profit monitoring.

## What It Does

- Connects to Binance through `ccxt`
- Uses Binance sandbox mode when `BINANCE_TEST_MODE=true`
- Detects recent high/low breakouts with volume spike confirmation
- Sends Telegram inline buttons: `ACCEPT` / `REJECT`
- Supports Telegram `/sleep` for controlled safe exit
- Executes confirmed market orders
- Monitors open positions and closes on SL or TP
- Logs trades to CSV

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` with:

- Binance testnet API key and secret
- Telegram bot token
- Your Telegram chat ID

For Binance testnet, create API keys from Binance testnet and keep:

```env
BINANCE_TEST_MODE=true
```

Switch to live trading only after testing:

```env
BINANCE_TEST_MODE=false
```

To test with a fixed virtual account size, regardless of the sandbox wallet balance:

```env
ACCOUNT_EQUITY_OVERRIDE=2000
```

For a short end-to-end test run:

```env
TEST_MODE=True
TEST_POLL_INTERVAL_SECONDS=10
TEST_SLEEP_EXIT_DELAY_SECONDS=600
TEST_TRADE_AMOUNT=0.001
TEST_FORCE_SIGNAL=true
```

In test mode the bot requires Binance sandbox mode, caps the base order size, uses DEBUG logging, and can force a BUY test signal when normal breakout conditions are absent.

## Logging

The bot uses Python logging with console output and `logs.txt`:

```text
[2026-01-01 12:00:00] [INFO] [EXECUTION] Order placed BTC/USDT BUY 0.001
```

With `TEST_MODE=True`, logs run at DEBUG level and include each loop iteration, strategy indicator values, raw API responses, position status, Telegram actions, order attempts, and sleep-mode liquidation steps.

Sleep mode can be triggered from Telegram:

```text
/sleep
```

When active, the bot immediately stops generating new trades, tightens tracked position exits to `SLEEP_STOP_LOSS_PCT` and `SLEEP_TAKE_PROFIT_PCT`, then closes all tracked positions and sells non-USDT balances after `SLEEP_EXIT_DELAY_SECONDS`.

With `TEST_MODE=True`, `/sleep` uses a 10-minute countdown and aggressive entry-based exits:

- BUY SL: `entry * 0.999`
- BUY TP: `entry * 1.005`

## Run

```powershell
python main.py
```

## Notes

- This starter is spot-oriented. A `SELL` signal will place a spot sell order and requires base asset inventory.
- Never run live trading before validating symbol precision, minimum order sizes, and exchange permissions.
- Telegram confirmations expire after `POLL_INTERVAL_SECONDS` loop progress only by design; accepted signals execute immediately.
