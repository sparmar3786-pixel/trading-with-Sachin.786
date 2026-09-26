import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from nifty_option_ai_live_v1.backend.adapters.groww import GrowwAdapter
from nifty_option_ai_live_v1.backend.adapters.dhan import DhanAdapter

def test_groww_normalization(monkeypatch):
    class FakeAPI:
        EXCHANGE_NSE='NSE'
        def get_expiries(self, **kw): return {'expiries':['2026-09-29']}
        def get_option_chain(self, **kw):
            return {'underlying_ltp':23000,'strikes':{'23000':{'CE':{'ltp':100,'open_interest':1000,'volume':500,'top_bid_price':99,'top_ask_price':101,'greeks':{'iv':12}},'PE':{'ltp':90,'open_interest':1200,'volume':600,'top_bid_price':89,'top_ask_price':91,'greeks':{'iv':13}}}}}
        def get_user_profile(self): return {'active_segments':['FNO']}
    a=GrowwAdapter('x'); a.api=FakeAPI(); out=a.option_chain('NIFTY')
    assert out['spot']==23000 and out['chain'][0]['ce']['oi']==1000
    assert out['chain'][0]['ce']['doi'] is None

def test_dhan_normalization(monkeypatch):
    a=DhanAdapter('c','t',{'NIFTY':13})
    responses=[{'data':['2026-09-29']},{'data':{'last_price':23000,'oc':{'23000.000000':{'ce':{'oi':110,'previous_oi':100,'volume':20,'last_price':100,'top_bid_price':99,'top_ask_price':101,'implied_volatility':12,'greeks':{}},'pe':{'oi':120,'previous_oi':100,'volume':30,'last_price':90,'top_bid_price':89,'top_ask_price':91,'implied_volatility':13,'greeks':{}}}}}}]
    a._post=lambda *args,**kwargs: responses.pop(0)
    out=a.option_chain('NIFTY')
    assert out['spot']==23000 and out['chain'][0]['ce']['doi']==10
