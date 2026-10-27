"""Paper submissions through the repository's existing KalshiAPI order method."""
from decimal import Decimal
from pathlib import Path
import sys
from uuid import NAMESPACE_URL, uuid5

SRC = Path(__file__).resolve().parents[1]/'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kalshi_api import KalshiAPI


class RepositoryPaperOrders:
    """Hardwired local transport. No real exchange order client is instantiated."""
    def __init__(self):
        self.prepared = None
        self.api = KalshiAPI(api_key='paper-simulation', base_url='paper://local',
                            max_retries=1, retry_delay=0, transport=self._paper_transport)

    def place(self, ticker, trade):
        if self.prepared is not None:
            raise RuntimeError('An order is already prepared')
        if trade['side'] not in ('yes','no') or not isinstance(trade['quantity'],int) or trade['quantity']<=0:
            raise ValueError('Invalid paper side or contract count')
        payload = dict(ticker=ticker, action='buy', side=trade['side'], type='limit',
                       count=trade['quantity'],
                       client_order_id='paper-'+str(uuid5(NAMESPACE_URL,ticker)),
                       **{trade['side']+'_price_dollars':format(Decimal(str(trade['price'])),'.4f')})
        self.prepared = dict(payload=payload, trade=dict(trade))
        try:
            # This is the actual, existing repository order interface.
            response = self.api.create_order(payload)
            if not response or response.get('mode')!='paper' or response.get('order',{}).get('status')!='executed':
                raise ValueError('No confirmed local simulated execution')
            return response['order']['order_id']
        finally:
            self.prepared = None

    def _paper_transport(self, method, endpoint, **kwargs):
        if method!='POST' or endpoint!='/portfolio/orders' or self.prepared is None:
            raise ValueError('Paper transport only accepts one prepared create_order request')
        if set(kwargs)!={'json'} or kwargs['json']!=self.prepared['payload']:
            raise ValueError('Order changed after simulation/risk checks')
        trade = self.prepared['trade']
        return dict(mode='paper', order=dict(order_id=self.prepared['payload']['client_order_id'],
                    status='executed', fill_count=trade['quantity'], remaining_count=0,
                    simulated_cost=trade['cost'], simulated_fee=trade['fee']))
