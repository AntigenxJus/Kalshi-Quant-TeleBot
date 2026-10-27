"""Research-only simulation. No credentials, exchange orders, or money movement."""
import math
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from zoneinfo import ZoneInfo

SERIES = {'KXSILVER15M': 'Silver', 'KXGOLD15M': 'Gold', 'KXWTI15M': 'WTI oil', 'KXCOPPER15M': 'Copper'}
WARMUP = 100
SIMULATED_BANKROLL = 10000.0
DAY_LIMIT = 500.0
ENTRY_STOP = 400.0
POSITION_LIMIT = 25.0
OPEN_LIMIT = 100.0
SLIPPAGE = 0.005
UNCERTAINTY = 0.05
MIN_EDGE = 0.02


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def trading_day(ts=None):
    # Windows may have no IANA database; use bundled tzdata when available.
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo('America/New_York')).date().isoformat()


def fee(quantity, price, multiplier=1):
    """Conservative whole-cent rounding, intentionally no fractional contracts."""
    return float((Decimal('0.07') * Decimal(str(multiplier)) * quantity * Decimal(str(price)) *
                  (1 - Decimal(str(price)))).quantize(Decimal('0.01'), rounding=ROUND_CEILING))


def book_view(payload):
    book = payload.get('orderbook_fp')
    if not book:
        raise ValueError('Fixed-point order book missing')
    def levels(name):
        result = [(float(p), float(q)) for p, q in book.get(name, [])]
        if any(not math.isfinite(p + q) or not 0 < p < 1 or q < 0 for p, q in result):
            raise ValueError('Invalid book prices or quantities')
        return sorted(((p, q) for p, q in result if q > 0), reverse=True)
    yes, no = levels('yes_dollars'), levels('no_dollars')
    if not yes or not no:
        raise ValueError('Two-sided book unavailable')
    bid, ask = yes[0][0], round(1 - no[0][0], 6)
    spread = round(ask - bid, 6)
    if spread < 0 or spread > 0.05:
        raise ValueError('Crossed book or spread exceeds 5 cents')
    yq, nq = sum(q for _, q in yes[:3]), sum(q for _, q in no[:3])
    return dict(bid=bid, ask=ask, no_ask=round(1-bid, 6),
                yes_size=no[0][1], no_size=yes[0][1], spread=spread,
                mid=(bid+ask)/2, imbalance=(yq-nq)/(yq+nq))


def features(view, seconds):
    mid = max(0.005, min(0.995, view['mid']))
    return [1.0, math.log(mid/(1-mid)), view['imbalance'], view['spread']*10, seconds/900]


def predict(weights, x):
    z = max(-20, min(20, sum(a*b for a, b in zip(weights, x))))
    return 1/(1+math.exp(-z))


def train(rows):
    """Regularized logistic correction of the market midpoint, frozen after warmup."""
    weights = [0., 1., 0., 0., 0.]
    prior = list(weights)
    for _ in range(700):
        gradient = [0.]*5
        for x, y in rows:
            error = predict(weights, x)-y
            for j in range(5):
                gradient[j] += error*x[j]/len(rows)
        for j in range(5):
            weights[j] -= 0.06*(gradient[j]+0.05*(weights[j]-prior[j]))
    return weights


def risk_totals(trades, day):
    # Count gross losses, without allowing wins to replenish the day's budget.
    losses = sum(max(0, -t['pnl']) for t in trades if t['status']=='settled' and t['settle_day']==day)
    exposure = sum(t['cost'] for t in trades if t['status']=='open')
    return losses, exposure


def candidate(probability, view, multiplier, trades, day):
    losses, exposure = risk_totals(trades, day)
    if losses + exposure >= ENTRY_STOP:
        return None, 'Daily risk stop: realized losses plus open cost reached $400'
    budget = min(POSITION_LIMIT, OPEN_LIMIT-exposure, ENTRY_STOP-losses-exposure,
                 SIMULATED_BANKROLL + sum(t.get('pnl') or 0 for t in trades)-exposure)
    choices = []
    for side, prob, ask, depth in [('yes', probability, view['ask'], view['yes_size']),
                                   ('no', 1-probability, view['no_ask'], view['no_size'])]:
        # Only consume up to 25% of displayed best-level depth; no reuse per window.
        size = min(int(depth*0.25), int(max(0, budget)/(ask+SLIPPAGE+0.02*multiplier)))
        while size > 0:
            fees = fee(size, ask, multiplier)
            cost = round(size*(ask+SLIPPAGE)+fees, 6)
            if cost <= budget + 1e-8:
                edge = prob-ask-SLIPPAGE-fees/size-UNCERTAINTY
                if edge >= MIN_EDGE:
                    choices.append(dict(side=side, quantity=size, price=ask, fee=fees,
                                        cost=cost, edge=edge, probability=prob))
                break
            size -= 1
    if not choices:
        return None, 'Skip: insufficient net edge, depth, cash, or risk capacity'
    best = max(choices, key=lambda x: x['edge'])
    return best, 'Experimental paper fill; hold to confirmed final settlement'
