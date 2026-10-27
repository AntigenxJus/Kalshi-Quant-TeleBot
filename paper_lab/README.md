# Kalshi Paper Lab

An automatic **paper-trading research prototype** for 15-minute silver, gold,
WTI oil, and copper markets. Reads public production market data, learns a small
statistical model, simulates entries, and tracks final outcomes. No Kalshi account,
API key, real-money order, or money movement is supported.

**No proven edge, guaranteed success rate, or daily income.** The hypothetical
$10,000 starting bankroll is only a simulation parameter, not the user's balance.
The requested maximum daily loss is modeled as $500: stop admitting risk at $400,
leaving $100 in reserve. No $2,000 daily profit quota is enforced.

## Repository order path

Integrated with `yllvar/Kalshi-Quant-TeleBot`, based on commit `148ca12`.
Paper submissions take this path:

```text
Lab strategy and risk checks
  -> RepositoryPaperOrders.place
  -> existing KalshiAPI.create_order
  -> existing KalshiAPI._handle_request
  -> injected local paper transport
  -> confirmed simulated execution
  -> persistent paper ledger
```

`src/kalshi_api.py` adds an optional callable transport. When supplied, it owns
the whole request; exceptions never fall through to HTTP. The default API
behavior is unchanged. The paper adapter hardwires a local transport and a
`paper://local` base URL, ignoring account credentials. It supports only one
prepared buy-YES or buy-NO order at a time and rejects changed payloads.

The existing `Trader.execute_trade()` currently only logs placeholder orders;
this lab reuses the actual repository API order interface directly. It does not
start the unrelated legacy strategy loop or Telegram service. No outbound
exchange order request is sent. The ledger changes only after a confirmed local
execution. Tests verify the existing method is called and HTTP remains unused.

## Existing GitHub repository

Run commands from the repository root. A source ZIP contains the complete
modified repository. A companion Git patch can be applied on its base revision
with `git am <path-to-patch>` in a clean checkout. Review before merging.
Runtime data is excluded from both artifacts.

No GitHub Secrets or Kalshi credentials are needed for the lab. CI checks only
this module; GitHub Actions is not the collector runtime.

## Optional Codespaces

When creating a Codespace with advanced options, select the **Kalshi Paper Lab**
configuration at `.devcontainer/paper-lab/devcontainer.json`. It installs the
module requirements, runs tests, and starts the dashboard on forwarded port 8765.
Keep the port private. If using another container, start it manually:

```sh
python paper_lab/app.py --host 0.0.0.0
```

Codespaces can stop after inactivity or usage limits. It is not guaranteed
continuous hosting. No recurring task or always-on deployment is created.
The startup log is `/tmp/kalshi-paper-lab.log`. Reuse the same Codespace to retain
observations, and download exports or copy `paper_lab/data/paper.sqlite3` before
deleting it. Never run two collectors against the same database.

References:
- https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace
- https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle

## Local run

Requires Python 3.10+ and HTTPS access to `external-api.kalshi.com`.
Install the module requirements, then start from the repository root:

```sh
python -m pip install -r paper_lab/requirements.txt
python paper_lab/app.py
```

Open http://localhost:8765. Leave the process and computer awake. Ctrl+C stops it;
running the same command resumes the saved ledger. The Pause button stops new
paper entries while quote recording and settlement checks continue.

One diagnostic scan with a separate data directory:

```sh
python paper_lab/app.py --once --data-dir scratch-scan
```

Offline checks (from the repository root):

```sh
cd paper_lab
python -m unittest discover -s tests -v
```

## Research method

- Polls public REST data every 15 seconds after each completed scan; actual scan
  duration adds to that interval. No streaming authentication is requested.
- Stores book snapshots and exact rules in SQLite. Bid/ask conversion follows
  the complementary YES/NO bid books; prices use fixed-point dollar fields.
- Collects one feature vector per commodity window, at the first eligible scan
  with 2–13 minutes left. Timing can differ after downtime; no backfill is invented.
- Uses midpoint log odds, three-level book imbalance, spread, and time left.
  **No independent underlying commodity price feed or LLM service is connected.**
  This tests a market-book probability correction, not a commodity price model.
- Each commodity model waits for its first 100 chronological windows to finalize,
  including at least ten outcomes of each class. Fits regularized logistic
  regression once and freezes the model. This is a warmup threshold, not a claim
  that 100 examples establish a profitable model.
- New predictions are saved before settlement. Only later, finalized exchange
  outcomes score them. Training samples never contribute to forward Brier scores.
  Midpoint Brier score is a paired baseline. Scores are descriptive, without a
  confidence interval, calibration guarantee, or statistical significance claim.
- Simulates one entry at most per market. Requires estimated profit of at least
  2 cents per contract after modeled fees, 0.5-cent slippage, and a fixed 5-cent
  uncertainty deduction. That deduction is **not** a statistical confidence bound.
- Supported quadratic taker fees use current series metadata plus effective event
  overrides, rounded up to cents conservatively. Unsupported fee structures or
  incomplete event fee history cause a skip. Series metadata is refreshed each scan.
- Consumes at most 25% of best-level displayed size, with whole contracts only.
  The charged slippage reduces P&L; it is not claimed to be an exchange fill.
- Holds to finalized YES/NO outcomes. Pending/disputed/unsupported outcomes retain
  open exposure; they are never guessed from a chart or from the last trade price.

## Risk rules

- $25 maximum loss per commodity window, inclusive of fees and slippage.
- $100 maximum open exposure across all commodities; no diversification credit.
- New cost must fit within $400 minus today's gross realized losses and all open
  exposure. Wins never replenish that allowance. The $500 budget includes a $100
  reserve. These are simulator constraints, not a real-money loss guarantee.
- Day boundaries use America/New_York, including daylight saving time. An open
  position carries into the next day's exposure. A loss counts on settlement day.
- Synthetic cash cannot go negative under admitted worst-case loss.
- API failures, unsupported books, spreads above 5 cents, slow responses, and
  paused exchange status block affected entries. The app does not assume metadata
  `updated_time` is a quote timestamp. REST round-trip checks cannot prove the
  upstream feed itself is fresh.

## Limitations and operations

This is a polling simulator, not a queue/latency-accurate exchange backtester.
Displayed liquidity can vanish. Book snapshots may lag. The model lacks an
underlying settlement-source feed, announcement calendar, calibrated uncertainty,
and regime detection. Treat all paper performance as exploratory. There is no
automatic authorization for live trading based on these results.

The dashboard assistant uses deterministic explanations of model/risk state;
it is not a connected language-model agent. No model API cost is incurred.

State and raw snapshots live in `data/paper.sqlite3`. Dashboard exports contain
all observations, frozen model weights/training tickers, trades, and recent logs;
the raw snapshots require copying the SQLite file. Trade CSV contains all trades.
Data grows with operation; monitor disk use. To start a separate experiment use
`--data-dir another-folder`, preserving the original ledger.

The HTTP server is intended for loopback or a private Codespaces forwarded port.
It uses per-process control tokens and no cross-origin access. Keep Codespaces
port access private; there is no standalone multi-user authentication system.

API references:
- https://docs.kalshi.com/getting_started/quick_start_market_data
- https://docs.kalshi.com/api-reference/market/get-market-orderbook
- https://docs.kalshi.com/api-reference/market/get-series
- https://docs.kalshi.com/api-reference/events/get-event-fee-changes
- https://kalshi.com/fee-schedule

## Files

`app.py`: collector, persistent state, HTTP server, settlement handling.
`engine.py`: fee, book, model, and risk calculations.
`index.html` / `ui.js`: local dashboard.
`tests/test_lab.py`: offline fixtures testing risk and lifecycle behavior.
`repository_orders.py`: adapter through the existing repository order API.
`.devcontainer/paper-lab/devcontainer.json`: optional Codespaces runtime.
`.github/workflows/paper-lab.yml`: offline CI checks.
