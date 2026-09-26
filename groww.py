from __future__ import annotations
import time
from datetime import date
from typing import Any

class GrowwAdapter:
    """Read-only market-data adapter using Groww's official Trading API.

    This adapter never asks for or stores a Groww web-app password. It accepts an
    official API access token and uses the documented option-chain endpoint/SDK.
    """
    name = "GROWW"

    def __init__(self, access_token: str):
        self.access_token = access_token.strip()
        self.api = None
        self.last_chain: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.last_update: float | None = None
        self._prev: dict[tuple[float,str], dict[str,float|None]] = {}

    def connect(self) -> dict[str, Any]:
        if not self.access_token:
            raise ValueError("Groww API access token is required.")
        try:
            from growwapi import GrowwAPI
            self.api = GrowwAPI(self.access_token)
            # Profile call validates the token without placing an order.
            profile = self.api.get_user_profile()
            self.last_error = None
            return {"connected": True, "profile": profile}
        except Exception as exc:
            self.api = None
            self.last_error = str(exc)
            raise

    def _expiry(self, underlying: str) -> str:
        expiries = self.api.get_expiries(
            exchange=self.api.EXCHANGE_NSE,
            underlying_symbol=underlying,
            year=date.today().year,
            month=date.today().month,
        )
        vals = expiries.get("expiries") if isinstance(expiries, dict) else expiries
        vals = [str(x) for x in (vals or []) if str(x) >= date.today().isoformat()]
        if not vals:
            # The API can return a full year if month is omitted.
            expiries = self.api.get_expiries(
                exchange=self.api.EXCHANGE_NSE,
                underlying_symbol=underlying,
            )
            vals = expiries.get("expiries") if isinstance(expiries, dict) else expiries
            vals = [str(x) for x in (vals or []) if str(x) >= date.today().isoformat()]
        if not vals:
            raise RuntimeError(f"No active {underlying} expiry returned by Groww.")
        return sorted(vals)[0]

    @staticmethod
    def _num(v):
        try:
            return None if v is None else float(v)
        except Exception:
            return None

    def option_chain(self, underlying: str = "NIFTY", expiry: str | None = None) -> dict[str, Any]:
        if self.api is None:
            self.connect()
        expiry = expiry or self._expiry(underlying)
        raw = self.api.get_option_chain(
            exchange=self.api.EXCHANGE_NSE,
            underlying=underlying,
            expiry_date=expiry,
        )
        strikes = raw.get("strikes", {}) if isinstance(raw, dict) else {}
        rows = []
        for strike_s, both in strikes.items():
            try:
                strike = float(strike_s)
            except Exception:
                continue
            both = both or {}
            row = {"strike": strike, "ce": {}, "pe": {}}
            for side in ("CE", "PE"):
                src = both.get(side) or both.get(side.lower()) or {}
                greeks = src.get("greeks") or {}
                oi = self._num(src.get("open_interest")); ltp = self._num(src.get("ltp"))
                prev = self._prev.get((strike, side.lower()), {})
                doi = (oi - prev.get("oi")) if oi is not None and prev.get("oi") is not None else None
                chg = (ltp - prev.get("ltp")) if ltp is not None and prev.get("ltp") is not None else None
                row[side.lower()] = {
                    "oi": oi,
                    "doi": doi,
                    "vol": self._num(src.get("volume")),
                    "iv": self._num(src.get("implied_volatility", greeks.get("iv"))),
                    "ltp": ltp,
                    "chg": chg,
                    "bid": self._num(src.get("top_bid_price")),
                    "ask": self._num(src.get("top_ask_price")),
                    "tradingSymbol": src.get("trading_symbol"),
                    "delta": self._num(greeks.get("delta")),
                    "theta": self._num(greeks.get("theta")),
                    "gamma": self._num(greeks.get("gamma")),
                    "vega": self._num(greeks.get("vega")),
                }
            rows.append(row)
        rows.sort(key=lambda x: x["strike"])
        for r in rows:
            for side in ("ce", "pe"):
                self._prev[(r["strike"], side)] = {"oi": r[side].get("oi"), "ltp": r[side].get("ltp")}
        now = time.time()
        self.last_update = now
        self.last_error = None
        self.last_chain = {
            "capturedAtEpoch": now,
            "capturedAt": __import__("datetime").datetime.fromtimestamp(now, __import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
            "spot": self._num(raw.get("underlying_ltp")),
            "expiry": expiry,
            "expiryDates": [expiry],
            "chain": rows,
            "source": "GROWW_OFFICIAL_API",
            "dataQuality": "Groww official option-chain API; ΔOI/change derived only from consecutive validated app snapshots",
        }
        return self.last_chain
