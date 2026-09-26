from __future__ import annotations

from groww import GrowwAdapter
from dhan import DhanAdapter


def test_groww_normalization():
    class FakeAPI:
        EXCHANGE_NSE = "NSE"

        def get_expiries(self, **kw):
            return {"expiries": ["2026-09-29"]}

        def get_option_chain(self, **kw):
            return {
                "underlying_ltp": 23000,
                "strikes": {
                    "23000": {
                        "CE": {
                            "ltp": 100,
                            "open_interest": 1000,
                            "volume": 500,
                            "top_bid_price": 99,
                            "top_ask_price": 101,
                            "greeks": {"iv": 12, "delta": 0.5},
                        },
                        "PE": {
                            "ltp": 90,
                            "open_interest": 1200,
                            "volume": 600,
                            "top_bid_price": 89,
                            "top_ask_price": 91,
                            "greeks": {"iv": 13, "delta": -0.5},
                        },
                    }
                },
            }

        def get_user_profile(self):
            return {"active_segments": ["FNO"]}

    adapter = GrowwAdapter("test-token")
    adapter.api = FakeAPI()
    out = adapter.option_chain("NIFTY")

    assert out["source"] == "GROWW_OFFICIAL_API"
    assert out["spot"] == 23000
    assert out["chain"][0]["strike"] == 23000
    assert out["chain"][0]["ce"]["oi"] == 1000
    assert out["chain"][0]["pe"]["oi"] == 1200
    assert out["chain"][0]["ce"]["delta"] == 0.5
    assert out["chain"][0]["pe"]["delta"] == -0.5
    assert out["chain"][0]["ce"]["doi"] is None


def test_dhan_normalization():
    adapter = DhanAdapter("client", "token", {"NIFTY": 13})
    responses = [
        {"data": ["2026-09-29"]},
        {
            "data": {
                "last_price": 23000,
                "oc": {
                    "23000.000000": {
                        "ce": {
                            "oi": 110,
                            "previous_oi": 100,
                            "volume": 20,
                            "last_price": 100,
                            "top_bid_price": 99,
                            "top_ask_price": 101,
                            "implied_volatility": 12,
                            "greeks": {},
                        },
                        "pe": {
                            "oi": 120,
                            "previous_oi": 100,
                            "volume": 30,
                            "last_price": 90,
                            "top_bid_price": 89,
                            "top_ask_price": 91,
                            "implied_volatility": 13,
                            "greeks": {},
                        },
                    }
                },
            }
        },
    ]
    adapter._post = lambda *args, **kwargs: responses.pop(0)
    out = adapter.option_chain("NIFTY")

    assert out["spot"] == 23000
    assert out["chain"][0]["ce"]["doi"] == 10
    assert out["chain"][0]["pe"]["doi"] == 20


if __name__ == "__main__":
    test_groww_normalization()
    test_dhan_normalization()
    print("CONNECTOR VALIDATION PASSED")
