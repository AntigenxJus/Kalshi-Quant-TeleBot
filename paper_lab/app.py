"""Local/Codespaces research dashboard. Upstream access is GET-only and public."""
import argparse
import copy
from contextlib import closing, contextmanager
import csv
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import engine as E
from repository_orders import RepositoryPaperOrders

ROOT = Path(__file__).resolve().parent
BASE = 'https://external-api.kalshi.com/trade-api/v2'
POLL = 15


def public_get(path):
    if not path.startswith(('/markets', '/series/', '/events/fee_changes', '/exchange/status')):
        raise ValueError('Only public market-data paths are allowed')
    req = urllib.request.Request(BASE+path, headers={'User-Agent': 'PaperLab/1.0 research', 'Cache-Control': 'no-cache'}, method='GET')
    started = time.time()
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.load(response)
    if time.time()-started > 5:
        raise ValueError('Response exceeded 5-second freshness budget')
    return result


def effective_fees(series, changes, now):
    kind, multiplier = series.get('fee_type'), series.get('fee_multiplier')
    if changes.get('cursor'):
        raise ValueError('Fee history paginated; refusing incomplete fee information')
    for change in sorted(changes.get('event_fee_changes', []), key=lambda c: c['scheduled_ts']):
        if E.timestamp(change['scheduled_ts']) <= now:
            kind = change.get('fee_type_override') or series.get('fee_type')
            value = change.get('fee_multiplier_override')
            multiplier = series.get('fee_multiplier') if value is None else value
    if kind not in ('quadratic', 'quadratic_with_maker_fees', 'quadratic_with_combo_maker_fees'):
        raise ValueError('Unsupported fee schedule; no simulated entry')
    multiplier = float(multiplier)
    if not 0 < multiplier <= 20:
        raise ValueError('Invalid fee multiplier')
    return multiplier


class Store:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory/'paper.sqlite3'
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS snapshots (ts REAL, ticker TEXT, payload TEXT)')

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as conn:
            with conn:
                yield conn

    def load(self):
        with self.connect() as conn:
            row = conn.execute('SELECT payload FROM state WHERE id=1').fetchone()
        return json.loads(row[0]) if row else dict(running=True, samples={}, trades=[], models={}, logs=[])

    def save(self, state):
        with self.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO state VALUES (1, ?)', (json.dumps(state, allow_nan=False),))

    def snapshot(self, ticker, payload):
        with self.connect() as conn:
            conn.execute('INSERT INTO snapshots VALUES (?, ?, ?)', (time.time(), ticker, json.dumps(payload)))


class Lab:
    def __init__(self, directory, get=public_get):
        self.store, self.get = Store(directory), get
        self.state = self.store.load()
        self.lock = threading.RLock()
        self.tick_lock = threading.Lock()
        self.stop = threading.Event()
        self.orders = RepositoryPaperOrders()
        self.cards = []
        self.health = 'Starting public-data collector'
        self.updated = None
        self.cached = None
        self.publish()

    def log(self, text):
        self.state['logs'].insert(0, dict(ts=time.time(), text=text))
        self.state['logs'] = self.state['logs'][:150]

    def publish(self):
        with self.lock:
            self.cached = dict(state=copy.deepcopy(self.state), cards=copy.deepcopy(self.cards),
                               health=self.health, updated=self.updated)

    def summary(self):
        with self.lock:
            data = copy.deepcopy(self.cached)
        state = data.pop('state')
        day = E.trading_day(time.time())
        losses, exposure = E.risk_totals(state['trades'], day)
        settled = [t for t in state['trades'] if t['status']=='settled']
        pnl = sum(t['pnl'] for t in settled)
        day_pnl = sum(t['pnl'] for t in settled if t['settle_day']==day)
        scored = [s for s in state['samples'].values() if s['label'] is not None and s['prediction'] is not None]
        def brier(field):
            return sum((s[field]-s['label'])**2 for s in scored)/len(scored) if scored else None
        models = []
        for series, name in E.SERIES.items():
            labels = [s for s in state['samples'].values() if s['series']==series and s['label'] is not None]
            models.append(dict(series=series, name=name, labels=len(labels),
                               trained=series in state['models'], target=E.WARMUP))
        equity, peak, drawdown = E.SIMULATED_BANKROLL, E.SIMULATED_BANKROLL, 0
        curve = [dict(ts=None, value=equity)]
        for trade in sorted(settled, key=lambda t: t['settled_at']):
            equity += trade['pnl']; peak = max(peak, equity)
            drawdown = max(drawdown, peak-equity)
            curve.append(dict(ts=trade['settled_at'], value=equity))
        return dict(**data, running=state['running'], day=day, pnl=pnl, day_pnl=day_pnl,
                    cash=E.SIMULATED_BANKROLL+pnl-exposure, exposure=exposure, losses=losses,
                    risk_used=losses+exposure, day_limit=E.DAY_LIMIT, entry_stop=E.ENTRY_STOP,
                    bankroll=E.SIMULATED_BANKROLL, samples=len(state['samples']), scored=len(scored),
                    brier=brier('prediction'), baseline_brier=brier('baseline'), models=models,
                    trades=state['trades'][-100:][::-1], logs=state['logs'], curve=curve[-300:],
                    max_drawdown=drawdown, wins=sum(t['pnl']>0 for t in settled), settled=len(settled))

    def toggle(self, running):
        # Serialize with tick so pause cannot race a simulated fill.
        with self.tick_lock:
            self.state['running'] = running
            self.log('Paper entries enabled' if running else 'Paper entries paused; recording and settlements continue')
            self.store.save(self.state)
            self.publish()

    def settle(self):
        # Includes samples with no position; only exchange-finalized outcomes label data.
        pending = [s for s in self.state['samples'].values() if s['label'] is None and s['close'] < time.time()]
        for sample in pending:
            if sample.get('retry_after', 0) > time.time():
                continue
            try:
                market = self.get('/markets/'+urllib.parse.quote(sample['ticker'], safe=''))['market']
                if market.get('status') != 'finalized' or market.get('result') not in ('yes','no'):
                    sample['retry_after'] = time.time()+60
                    continue
                outcome = int(market['result']=='yes')
                sample['label'] = outcome
                sample['settlement'] = market
                for trade in self.state['trades']:
                    if trade['ticker']==sample['ticker'] and trade['status']=='open':
                        win = (trade['side']=='yes') == bool(outcome)
                        trade.update(status='settled', settled_at=time.time(), settle_day=E.trading_day(time.time()),
                                     pnl=round(trade['quantity']*int(win)-trade['cost'], 6), result=market['result'])
                        self.log(f"Settled {trade['ticker']}: {trade['pnl']:+.2f} simulated dollars")
            except Exception as error:
                sample['retry_after'] = time.time()+120
                self.log(f"Settlement pending for {sample['ticker']}: {type(error).__name__}: {error}")

    def fit_ready_models(self):
        for series in E.SERIES:
            if series in self.state['models']:
                continue
            samples = sorted((s for s in self.state['samples'].values() if s['series']==series), key=lambda s:s['observed'])
            # Earliest chronological block must be completely finalized before training.
            block = samples[:E.WARMUP]
            if len(block)<E.WARMUP or any(s['label'] is None for s in block):
                continue
            positives = sum(s['label'] for s in block)
            if not 10 <= positives <= E.WARMUP-10:
                continue
            weights = E.train([(s['x'],s['label']) for s in block])
            self.state['models'][series] = dict(weights=weights, trained_at=time.time(),
                                               training_tickers=[s['ticker'] for s in block])
            self.log(f'{series}: experimental model frozen after {E.WARMUP} chronological training samples')

    def observe_series(self, series):
        metadata = self.get('/series/'+series)['series']
        if metadata.get('frequency')!='fifteen_min':
            raise ValueError('Series frequency is not fifteen_min')
        page = self.get('/markets?'+urllib.parse.urlencode(dict(series_ticker=series, status='open', limit=100)))
        if page.get('cursor'):
            raise ValueError('Market list incomplete; skipping series')
        markets = sorted(page['markets'], key=lambda m:m['close_time'])
        now = time.time()
        markets = [m for m in markets if E.timestamp(m['open_time'])<=now<E.timestamp(m['close_time'])]
        if not markets:
            return dict(series=series, name=E.SERIES[series], reason='No active market returned', status='Waiting')
        market = markets[0]
        ticker = market['ticker']
        close = E.timestamp(market['close_time'])
        seconds = close-time.time()
        card = dict(series=series, name=E.SERIES[series], ticker=ticker, close=close,
                    rules=market.get('rules_primary','')+' '+market.get('rules_secondary',''),
                    target=market.get('floor_strike'), status='Observe')
        if market.get('status')!='active' or market.get('market_type')!='binary' or float(market.get('notional_value_dollars',0))!=1:
            raise ValueError('Only active $1 binary markets are supported')
        if not market.get('rules_primary') or market.get('settlement_bounds_type','default')!='default':
            raise ValueError('Missing rules or unsupported settlement bounds')
        changes = self.get('/events/fee_changes?'+urllib.parse.urlencode(dict(event_ticker=market['event_ticker'],limit=100)))
        multiplier = effective_fees(metadata, changes, time.time())
        # Last network request before a paper fill is always a fresh book.
        requested = time.time()
        payload = self.get('/markets/'+urllib.parse.quote(ticker, safe='')+'/orderbook?depth=10')
        received = time.time()
        if received-requested>5:
            raise ValueError('Book round trip too slow')
        view = E.book_view(payload)
        self.store.snapshot(ticker, dict(market=market, book=payload, series=metadata, fee_changes=changes,
                                        requested=requested, received=received))
        card.update(view=view, received=received, fee_multiplier=multiplier)
        sample = self.state['samples'].get(ticker)
        if sample:
            card.update(status='Sampled', reason=sample['reason'], prediction=sample['prediction'])
            return card
        if not 120 <= close-time.time() <= 780:
            card.update(reason='Sample once per window, with 2–13 minutes remaining')
            return card
        x = E.features(view, close-received)
        model = self.state['models'].get(series)
        probability = E.predict(model['weights'],x) if model else None
        sample = dict(ticker=ticker, series=series, observed=received, close=close, x=x,
                      baseline=view['mid'], prediction=probability, label=None,
                      reason='Warmup: collect 100 finalized chronological windows per commodity',
                      view=view, fee_multiplier=multiplier)
        if probability is not None:
            trade, reason = E.candidate(probability, view, multiplier, self.state['trades'], E.trading_day(received))
            sample['reason'] = reason
            if not self.state['running']:
                trade = None
                sample['reason'] = 'Paper entries paused'
            if trade:
                # Use the repository's order path with a local, fail-closed transport.
                # The ledger changes only after a confirmed simulated execution.
                order_id = self.orders.place(ticker, trade)
                trade.update(ticker=ticker, series=series, opened=received, open_day=E.trading_day(received),
                             status='open', settle_day=None, pnl=None, model_trained_at=model['trained_at'],
                             order_id=order_id, execution_path='KalshiAPI.create_order → local paper transport')
                self.state['trades'].append(trade)
                self.log(f"PAPER {trade['side'].upper()} {trade['quantity']} {ticker}; max loss ${trade['cost']:.2f}")
        self.state['samples'][ticker] = sample
        card.update(status='Sampled', reason=sample['reason'], prediction=probability)
        return card

    def tick(self):
        with self.tick_lock:
            self.settle()
            self.fit_ready_models()
            cards = []
            errors = 0
            try:
                status = self.get('/exchange/status')
                if not status.get('exchange_active') or not status.get('trading_active'):
                    raise ValueError('Exchange paused; entries skipped')
                for series in E.SERIES:
                    try:
                        cards.append(self.observe_series(series))
                    except Exception as error:
                        errors += 1
                        cards.append(dict(series=series, name=E.SERIES[series], status='Skip',
                                          reason=f'{type(error).__name__}: {error}'))
            except Exception as error:
                errors += 1
                self.log(str(error))
            self.cards = cards
            self.updated = time.time()
            self.health = f'Public data connected · {len(cards)-errors if cards else 0} series available' if not errors else 'Data issue · affected entries skipped'
            self.store.save(self.state)
            self.publish()

    def loop(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as error:
                self.health = f'Collector error: {type(error).__name__}: {error}'
                self.log(self.health)
                self.store.save(self.state)
                self.publish()
            self.stop.wait(POLL)


def handler_factory(lab):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, content_type='application/json', status=200, attachment=None):
            if not isinstance(body, bytes):
                body = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type+'; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
            if attachment:
                self.send_header('Content-Disposition',f'attachment; filename="{attachment}"')
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            host = self.headers.get('Host','').split(':')[0].lower()
            return host in ('localhost','127.0.0.1','0.0.0.0') or host.endswith('.app.github.dev')

        def do_GET(self):
            if not self.valid_host():
                return self.reply('Forbidden host','text/plain',403)
            path = urllib.parse.urlsplit(self.path).path
            if path in ('/','/index.html'):
                return self.reply((ROOT/'index.html').read_text('utf-8').replace('__TOKEN__',token),'text/html')
            if path == '/ui.js':
                return self.reply((ROOT/'ui.js').read_bytes(),'text/javascript')
            if path == '/api/state':
                return self.reply(json.dumps(lab.summary(), allow_nan=False))
            if path == '/api/export':
                # Immutable published snapshot, never read mutable worker state here.
                with lab.lock:
                    export = copy.deepcopy(lab.cached['state'])
                return self.reply(json.dumps(export,indent=2),'application/json',attachment='paper-lab-export.json')
            if path == '/api/trades.csv':
                with lab.lock:
                    trades = copy.deepcopy(lab.cached['state']['trades'])
                fields = ['ticker','side','quantity','price','fee','cost','status','pnl','opened','settle_day']
                out = io.StringIO(newline='')
                writer = csv.DictWriter(out,fieldnames=fields,extrasaction='ignore')
                writer.writeheader(); writer.writerows(trades)
                return self.reply(out.getvalue(),'text/csv',attachment='paper-trades.csv')
            return self.reply('Not found','text/plain',404)

        def do_POST(self):
            if not self.valid_host() or not secrets.compare_digest(self.headers.get('X-Paper-Token',''),token):
                return self.reply('Forbidden','text/plain',403)
            if self.path not in ('/api/start','/api/pause'):
                return self.reply('Not found','text/plain',404)
            lab.toggle(self.path=='/api/start')
            return self.reply('{"ok":true}')
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data-dir',type=Path,default=Path(os.environ.get('PAPER_LAB_DATA',ROOT/'data')))
    parser.add_argument('--once',action='store_true',help='One public-data scan, save, print summary, exit')
    args = parser.parse_args()
    # Fail early with actionable guidance if Windows lacks its timezone database.
    try:
        E.trading_day(time.time())
    except Exception:
        raise SystemExit('Timezone database missing. Run: python -m pip install tzdata')
    lab = Lab(args.data_dir)
    if args.once:
        lab.tick(); print(json.dumps(lab.summary(),indent=2)); return
    server = ThreadingHTTPServer((args.host,args.port),handler_factory(lab))
    threading.Thread(target=lab.loop,daemon=True).start()
    print(f'Paper Lab: http://localhost:{args.port} | simulated funds only',flush=True)
    print(f'Ledger: {lab.store.path}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        lab.stop.set()
        server.server_close()


if __name__=='__main__':
    main()
