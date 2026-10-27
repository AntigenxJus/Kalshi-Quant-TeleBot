import copy
from pathlib import Path
import tempfile
import time
import unittest

import app
import engine as E


class EngineTests(unittest.TestCase):
    def view(self):
        return dict(ask=.50, no_ask=.51, yes_size=10000, no_size=10000)

    def test_fixed_point_book_complements_and_sorts(self):
        v = E.book_view({'orderbook_fp':{'yes_dollars':[['0.40','20'],['0.45','12.5']],
                                        'no_dollars':[['0.50','18'],['0.53','8']]}})
        self.assertEqual(v['ask'],.47)
        self.assertEqual(v['no_ask'],.55)
        self.assertEqual(v['yes_size'],8)
        self.assertAlmostEqual(v['spread'],.02)

    def test_missing_crossed_and_invalid_books_are_rejected(self):
        for y,n in [([],[]),([['.8','1']],[['.8','2']]),([['NaN','3']],[['.5','1']])]:
            with self.assertRaises(ValueError):
                E.book_view({'orderbook_fp':{'yes_dollars':y,'no_dollars':n}})

    def test_fee_rounds_up(self):
        self.assertEqual(E.fee(100,.5),1.75)
        self.assertEqual(E.fee(1,.5),.02)

    def test_high_win_rate_can_be_unprofitable(self):
        self.assertLess(.95-.96-E.fee(100,.96)/100,0)

    def test_position_cost_includes_fees_and_slippage(self):
        trade,_ = E.candidate(.95,self.view(),1,[],'2026-10-27')
        self.assertIsNotNone(trade)
        self.assertLessEqual(trade['cost'],25)
        self.assertAlmostEqual(trade['cost'],trade['quantity']*(trade['price']+.005)+trade['fee'])

    def test_gross_losses_and_open_positions_stop_entries(self):
        trades=[dict(status='settled',pnl=-390,settle_day='2026-10-27'),
                dict(status='settled',pnl=1000,settle_day='2026-10-27'),
                dict(status='open',cost=10)]
        trade,_=E.candidate(.95,self.view(),1,trades,'2026-10-27')
        self.assertIsNone(trade)

    def test_risk_capacity_cannot_be_overshot(self):
        trades=[dict(status='settled',pnl=-399,settle_day='2026-10-27')]
        trade,_=E.candidate(.95,self.view(),1,trades,'2026-10-27')
        if trade:
            self.assertLessEqual(trade['cost'],1)

    def test_depth_cap_and_aggregate_cap(self):
        v=self.view();v['yes_size']=8
        trade,_=E.candidate(.95,v,1,[],'2026-10-27')
        self.assertEqual(trade['quantity'],2)
        trade,_=E.candidate(.95,self.view(),1,[dict(status='open',cost=100)],'2026-10-27')
        self.assertIsNone(trade)

    def test_midnight_retains_prior_day_exposure(self):
        self.assertEqual(E.risk_totals([dict(status='open',cost=90)],'2026-10-28'),(0,90))
        self.assertEqual(E.trading_day(1793145600),'2026-10-27')

    def test_fee_override_and_clear(self):
        series=dict(fee_type='quadratic',fee_multiplier=1)
        changes=dict(event_fee_changes=[dict(scheduled_ts='2026-10-27T00:00:00Z',fee_type_override='quadratic',fee_multiplier_override=2)])
        self.assertEqual(app.effective_fees(series,changes,E.timestamp('2026-10-27T12:00:00Z')),2)
        changes['event_fee_changes'].append(dict(scheduled_ts='2026-10-27T01:00:00Z',fee_type_override=None,fee_multiplier_override=None))
        self.assertEqual(app.effective_fees(series,changes,E.timestamp('2026-10-27T12:00:00Z')),1)
        with self.assertRaises(ValueError):
            app.effective_fees(dict(fee_type='flat',fee_multiplier=1),{},time.time())


class LabTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=time.time()
        self.market=dict(ticker='KXSILVER15M-TEST-30',event_ticker='KXSILVER15M-TEST',
                         open_time=self.iso(self.now-200),close_time=self.iso(self.now+700),
                         market_type='binary',notional_value_dollars='1.0000',status='active',
                         rules_primary='Test fixture: official outcome will determine the binary payout.',
                         settlement_bounds_type='default',result='')
        self.fail=False
        self.book={'orderbook_fp':{'yes_dollars':[['.49','10000']],'no_dollars':[['.50','10000']]}}
        self.lab=app.Lab(Path(self.tmp.name),get=self.get)

    def tearDown(self):
        self.tmp.cleanup()

    def iso(self,ts):
        from datetime import datetime,timezone
        return datetime.fromtimestamp(ts,timezone.utc).isoformat()

    def get(self,path):
        if self.fail: raise OSError('fixture outage')
        if path.startswith('/exchange/status'): return dict(exchange_active=True,trading_active=True)
        if path.endswith('/orderbook?depth=10'): return copy.deepcopy(self.book)
        if path.startswith('/events/fee_changes'): return dict(event_fee_changes=[],cursor='')
        if path.startswith('/series/'): return dict(series=dict(fee_type='quadratic',fee_multiplier=1,frequency='fifteen_min'))
        if path.startswith('/markets?'):
            if 'KXSILVER15M' in path: return dict(markets=[copy.deepcopy(self.market)],cursor='')
            return dict(markets=[],cursor='')
        return dict(market=copy.deepcopy(self.market))

    def test_warmup_never_fills(self):
        self.lab.tick()
        self.assertEqual(len(self.lab.state['samples']),1)
        self.assertEqual(self.lab.state['trades'],[])
        self.assertIsNone(next(iter(self.lab.state['samples'].values()))['prediction'])

    def test_restart_preserves_position_and_prevents_duplicate_fill(self):
        self.lab.state['models']['KXSILVER15M']=dict(weights=[5,0,0,0,0],trained_at=self.now-1)
        self.lab.tick();self.lab.tick()
        self.assertEqual(len(self.lab.state['trades']),1)
        second=app.Lab(Path(self.tmp.name),get=self.get)
        second.tick()
        self.assertEqual(len(second.state['trades']),1)

    def test_only_finalized_exchange_outcome_settles(self):
        self.lab.state['models']['KXSILVER15M']=dict(weights=[5,0,0,0,0],trained_at=self.now-1)
        self.lab.tick()
        sample=next(iter(self.lab.state['samples'].values()));sample['close']=self.now-1
        self.market.update(status='determined',result='yes')
        self.lab.settle()
        self.assertEqual(self.lab.state['trades'][0]['status'],'open')
        sample['retry_after']=0;self.market['status']='finalized'
        self.lab.settle()
        t=self.lab.state['trades'][0]
        self.assertAlmostEqual(t['pnl'],t['quantity']-t['cost'])
        self.assertEqual(sample['label'],1)

    def test_outage_retains_open_risk(self):
        self.lab.state['models']['KXSILVER15M']=dict(weights=[5,0,0,0,0],trained_at=self.now-1)
        self.lab.tick();self.fail=True;self.lab.tick()
        self.assertEqual(len(self.lab.state['trades']),1)
        self.assertGreater(self.lab.summary()['exposure'],0)

    def test_pause_blocks_entries_but_keeps_observations(self):
        self.lab.state['models']['KXSILVER15M']=dict(weights=[5,0,0,0,0],trained_at=self.now-1)
        self.lab.toggle(False);self.lab.tick()
        self.assertEqual(self.lab.state['trades'],[])
        self.assertEqual(len(self.lab.state['samples']),1)

    def test_training_and_forward_samples_do_not_mix(self):
        for i in range(100):
            self.lab.state['samples'][str(i)]=dict(ticker=str(i),series='KXSILVER15M',observed=i,
                label=i%2,x=[1,0,0,.1,.8],prediction=None,baseline=.5)
        self.lab.fit_ready_models()
        model=copy.deepcopy(self.lab.state['models']['KXSILVER15M'])
        self.lab.tick()
        self.assertIsNotNone(self.lab.state['samples'][self.market['ticker']]['prediction'])
        self.lab.fit_ready_models()
        self.assertEqual(model,self.lab.state['models']['KXSILVER15M'])
        self.assertEqual(len(model['training_tickers']),100)


if __name__=='__main__':
    unittest.main()
