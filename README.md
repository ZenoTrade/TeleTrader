# TeleTrader

A **Telegram-to-MetaTrader 5 trading bot** with multi-user support, copy-trading, and a desktop admin panel.

It listens to a Telegram signal channel, parses trading signals, and executes orders on multiple MT5 accounts simultaneously — each with personal risk settings.

```
XAUUSD - BUY NOW
Entry : 3213
Targets :
3216
3220
3225
3230
Stoploss : 3200
@Jasin Trader:Nemat
```

The bot automatically:

1. Parses signal messages from your Telegram channel.
2. Splits each signal into one order per take-profit target.
3. Distributes risk across targets using Fibonacci weighting.
4. Executes orders on every enabled user account (scaled to each user's personal risk).
5. Moves SL to TP1 when the first target is hit (protects remaining positions).
6. Copy-trades: any manual change you make on the master account (move SL/TP, close, partial close) is replicated to all follower accounts in real time.
7. Replies in Telegram with per-account execution results.

```
Acc 3575745: BUY 0.5% XAUUSD -> TP 3220.0 : OK
Acc 3575745: BUY 0.3% XAUUSD -> TP 3225.0 : OK
Acc 3575745: BUY 0.2% XAUUSD -> TP 3230.0 : OK
```

---

## Architecture

| Layer              | Location                        | What it does                                                                 |
| ------------------ | ------------------------------- | ---------------------------------------------------------------------------- |
| **Domain**         | `tradebot/domain/`             | Immutable models (`Signal`, `Order`, `Target`) and port interfaces.          |
| **Application**    | `tradebot/application/`        | Signal parser, order generator, risk managers (Simple, Fibonacci).           |
| **Infrastructure** | `tradebot/infrastructure/`     | MT5 engine, Telegram listener, SL manager, copy-trade syncer, SQLite DB.    |
| **Admin Panel**    | `admin_panel.py`               | Native desktop app (tkinter) for managing users, accounts, and roles.        |
| **Entry-point**    | `main.py`                      | Wires everything together and starts the bot.                                |
| **Config**         | `config.py` + `.env`           | Pydantic v2 settings from environment variables.                             |
| **Tests**          | `tests/`                       | pytest suite for parser, order generator, and risk managers.                 |
| **Logging**        | `tradebot.log`                 | Loguru with 25 MB rotation.                                                  |

---

## Quick Start

### 1. Clone and create virtual environment

```powershell
git clone <repo-url>
cd TeleTrader
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure Telegram settings

Create a `.env` file in the project root:

```ini
TELEGRAM_TOKEN=123456:ABC-your-bot-token
SIGNAL_CHAT_ID=-1001234567890

# Global defaults (used for new users)
RISK_PER_TRADE=0.01
MAX_SLIPPAGE=20
MAGIC_NUMBER=32001
```

- `TELEGRAM_TOKEN` — your Telegram bot token from @BotFather.
- `SIGNAL_CHAT_ID` — the chat/channel ID the bot reads signals from.
- `RISK_PER_TRADE` — default risk per signal as a fraction (0.01 = 1% of balance).
- `MAX_SLIPPAGE` — max allowed price deviation in points (20 is standard for gold).

### 3. Add user accounts via admin panel

```powershell
python admin_panel.py
```

This opens the desktop admin panel. For each user, click **Add User** and enter:

- **First Name / Last Name / Phone** — user identity.
- **MT5 Account** — the login number (e.g., `3575745`).
- **MT5 Password** — the account password.
- **MT5 Server** — the broker server name (e.g., `AMarkets-Real`).
- **MT5 Path** — path to `terminal64.exe` (pre-filled with default).
- **Risk Per Trade (%)** — personal risk for this user (e.g., `1.0` for 1%).
- **Max Slippage** — price tolerance in points (default `20`).

Then select your own account and click **Set as Master**.

### 4. Launch the bot

```powershell
python -m main
```

Keep the MT5 terminal open on the server. The bot will:
- Validate all user accounts can log in.
- Start listening for Telegram signals.
- Start the SL manager (auto-moves SL to TP1).
- Start the copy-trade syncer (mirrors master account changes to followers).

### 5. Test with a signal

Post a formatted signal in your Telegram channel. The bot will parse it and execute orders on all enabled accounts.

---

## Admin Panel

Run `python admin_panel.py` to manage users.

| Button           | What it does                                                    |
| ---------------- | --------------------------------------------------------------- |
| **Add User**     | Opens form to add a new user with MT5 credentials and risk.     |
| **Edit**         | Edit the selected user's details.                               |
| **Delete**       | Remove the selected user (with confirmation).                   |
| **Toggle Enable**| Enable or disable a user without deleting them.                 |
| **Set as Master**| Mark the selected user as the master (copy-trade source).       |
| **Test Connection** | Verify MT5 login works for the selected user.                |
| **Refresh**      | Reload the user list from the database.                         |

Users are stored in `users.db` (SQLite), not `accounts.json`.

---

## Copy-Trading

TeleTrader includes a built-in copy-trade engine:

- One account is designated as **Master** (your account).
- All other enabled accounts are **Followers**.
- Every 5 seconds, the bot checks the master account for changes.
- Any action on the master is replicated to all followers:

| Master action        | Follower action                                          |
| -------------------- | -------------------------------------------------------- |
| Open a new position  | Open same position, volume scaled by follower's risk     |
| Modify SL            | SL updated on matching follower position                 |
| Modify TP            | TP updated on matching follower position                 |
| Partial close         | Proportional volume closed on follower                   |
| Full close           | Matching follower position closed                        |

Volume scaling formula: `follower_volume = master_volume * (follower_risk / master_risk)`

---

## Signal Format

The bot recognizes signals in this format:

```
SYMBOL - SIDE TYPE
Entry : PRICE
Targets :
PRICE_1
PRICE_2
PRICE_3
Stoploss : PRICE
@TraderName
```

- **First line**: `SYMBOL - BUY/SELL NOW/LIMIT/MARKET`
- **Entry**: the entry price.
- **Targets**: one price per line after "Targets".
- **Stoploss**: optional stop-loss price.
- **Comment**: optional `@TraderName` line; bot appends `1ofN`, `2ofN` per target.

### Examples

```
XAUUSD - BUY NOW
Entry : 3213
Targets :
3216
3220
3225
Stoploss : 3200
@Sniper
```

```
EURUSD - SELL LIMIT
Entry : 1.1000
Targets :
1.0985
1.0970
Stoploss : 1.1020
@Alice
```

---

## Risk Management

Risk is managed per user with Fibonacci-weighted target distribution:

- Each user has a personal `risk_per_trade` (e.g., 1% of their balance).
- The total risk is split across targets using Fibonacci weights (reversed: largest allocation to TP1).
- Volume is calculated from: `risk_money / (value_per_point * SL_distance)`.
- If no SL is provided, volume is zero and the order is skipped.

For a signal with 3 targets and 1% total risk, the split is approximately:
- TP1: 0.5%, TP2: 0.3%, TP3: 0.2%

---

## Auto SL Management

When TP1 is hit on any account:
- The bot detects the TP1 closure.
- It moves the SL of remaining positions (TP2, TP3, ...) to the TP1 price.
- This locks in profit: even if TP2 is never reached, the trade closes at TP1 instead of the original SL.

---

## Project Structure

```
TeleTrader/
  .env                    # Telegram token, chat ID, global defaults
  users.db                # SQLite database (created by admin panel)
  main.py                 # Bot entrypoint
  admin_panel.py          # Desktop admin panel (tkinter)
  config.py               # Pydantic settings
  requirements.txt        # Python dependencies
  tradebot/
    domain/
      models.py           # Signal, Order, Target, OrderResult
      ports.py            # Abstract interfaces
    application/
      parser.py           # Signal text parser
      order_generator.py  # Signal -> Orders
      risk.py             # Risk managers (Simple, Fibonacci)
    infrastructure/
      db.py               # SQLite CRUD for users + copy-map
      mt5_engine.py       # MT5 order execution
      sl_manager.py       # Auto SL-to-TP1 manager
      copy_syncer.py      # Copy-trade synchronizer
      telegram_listener.py
      telegram_notifier.py
      _mt5_utils.py       # MT5 init/login helpers
      _mt5_symbol_resolver.py
      _magic.py
  tests/
    test_parser.py
    test_order_generator.py
    test_risk_managers.py
```

---

## Running Tests

```powershell
pytest -q
```

---

## Requirements

- **Python 3.11+**
- **Windows** (MetaTrader5 Python package is Windows-only)
- **MT5 terminal** installed and open on the machine
- **Telegram bot** with access to your signal channel

Install dependencies:

```powershell
pip install -r requirements.txt
```
