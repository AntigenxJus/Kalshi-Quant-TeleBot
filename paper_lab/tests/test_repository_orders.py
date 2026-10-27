import unittest
from unittest.mock import patch

from repository_orders import KalshiAPI, RepositoryPaperOrders


class RepositoryOrderTests(unittest.TestCase):
    def trade(self,side='yes'):
        return dict(side=side,quantity=20,price=.501,cost=10.48,fee=.35)

    def test_existing_create_order_method_is_exercised_without_http(self):
        gateway=RepositoryPaperOrders()
        original=KalshiAPI.create_order
        with patch('requests.request',side_effect=AssertionError('HTTP orders prohibited')) as network:
            with patch.object(KalshiAPI,'create_order',autospec=True,side_effect=original) as existing:
                order_id=gateway.place('KXSILVER15M-FIXTURE-30',self.trade())
                existing.assert_called_once()
                payload=existing.call_args.args[1]
                self.assertEqual(payload['yes_price_dollars'],'0.5010')
                self.assertNotIn('no_price_dollars',payload)
                self.assertTrue(order_id.startswith('paper-'))
            network.assert_not_called()

    def test_no_contract_is_buy_no_not_a_short_yes(self):
        gateway=RepositoryPaperOrders()
        with patch.object(gateway.api,'create_order',wraps=gateway.api.create_order) as create:
            gateway.place('KXSILVER15M-FIXTURE-30',self.trade('no'))
            payload=create.call_args.args[0]
            self.assertEqual(payload['action'],'buy')
            self.assertEqual(payload['side'],'no')
            self.assertIn('no_price_dollars',payload)

    def test_transport_failure_never_falls_back_to_network(self):
        gateway=RepositoryPaperOrders()
        with patch('requests.request',side_effect=AssertionError('HTTP orders prohibited')) as network:
            with self.assertRaises(ValueError):
                gateway.api.cancel_order('unprepared-order')
            self.assertIsNone(gateway.prepared)
            network.assert_not_called()

    def test_default_existing_http_behavior_is_preserved(self):
        api=KalshiAPI(api_key='fixture',base_url='https://example.invalid',max_retries=1)
        with patch('requests.request') as request:
            request.return_value.content=b'{}'
            request.return_value.json.return_value={'order':{'order_id':'fixture'}}
            payload={'ticker':'FIXTURE','side':'yes'}
            self.assertEqual(api.create_order(payload)['order']['order_id'],'fixture')
            self.assertEqual(request.call_args.args[:2],('POST','https://example.invalid/portfolio/orders'))
            self.assertEqual(request.call_args.kwargs['json'],payload)

    def test_rejected_execution_does_not_enter_ledger(self):
        from pathlib import Path
        import tempfile
        import app
        with tempfile.TemporaryDirectory() as folder:
            lab=app.Lab(Path(folder))
            with patch.object(lab.orders.api,'create_order',return_value=None):
                with self.assertRaises(ValueError):
                    lab.orders.place('FIXTURE',self.trade())
            self.assertEqual(lab.state['trades'],[])


if __name__=='__main__':
    unittest.main()
