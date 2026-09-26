from __future__ import annotations
import time
import httpx
from datetime import datetime, timezone

class DhanAdapter:
    """Read-only DhanHQ v2 option-chain adapter.

    Underlying security IDs are configurable because they are broker master-data
    values. No order API is called by this adapter.
    """
    name = "DHAN"
    URL = "https://api.dhan.co/v2"

    def __init__(self, client_id: str, access_token: str, underlying_ids: dict[str, int] | None = None):
        self.client_id = client_id.strip()
        self.access_token = access_token.strip()
        self.underlying_ids = underlying_ids or {"NIFTY": 13}
        self.last_chain = None
        self.last_error = None
        self.last_update = None

    def _headers(self):
        return {"access-token": self.access_token, "client-id": self.client_id, "Content-Type": "application/json"}

    def _post(self, path, payload):
        with httpx.Client(timeout=12.0) as c:
            r = c.post(self.URL + path, headers=self._headers(), json=payload)
            r.raise_for_status()
            return r.json()

    def connect(self):
        if not self.client_id or not self.access_token:
            raise ValueError("Dhan client ID and access token are required.")
        self._post("/optionchain/expirylist", {"UnderlyingScrip": int(self.underlying_ids["NIFTY"]), "UnderlyingSeg": "IDX_I"})
        self.last_error = None
        return {"connected": True}

    @staticmethod
    def _num(v):
        try: return None if v is None else float(v)
        except Exception: return None

    def option_chain(self, underlying="NIFTY", expiry=None):
        if underlying not in self.underlying_ids:
            raise ValueError(f"No Dhan security ID configured for {underlying}.")
        uid = int(self.underlying_ids[underlying])
        exps = self._post("/optionchain/expirylist", {"UnderlyingScrip": uid, "UnderlyingSeg": "IDX_I"})
        vals = list((exps.get("data") or []))
        if expiry is None:
            today = datetime.now().date().isoformat()
            vals = sorted(str(x) for x in vals if str(x) >= today)
            if not vals: raise RuntimeError("Dhan returned no active expiry.")
            expiry = vals[0]
        raw = self._post("/optionchain", {"UnderlyingScrip": uid, "UnderlyingSeg": "IDX_I", "Expiry": expiry})
        data = raw.get("data") or {}
        rows=[]
        for strike_s,both in (data.get("oc") or {}).items():
            try: strike=float(strike_s)
            except Exception: continue
            row={"strike":strike,"ce":{},"pe":{}}
            for key,side in (("ce","ce"),("pe","pe")):
                src=(both or {}).get(side) or {}
                g=src.get("greeks") or {}
                row[key]={"oi":self._num(src.get("oi")),"doi":self._num(src.get("oi",0))-self._num(src.get("previous_oi",0)),"vol":self._num(src.get("volume")),"iv":self._num(src.get("implied_volatility")),"ltp":self._num(src.get("last_price")),"chg":None,"bid":self._num(src.get("top_bid_price")),"ask":self._num(src.get("top_ask_price")),"tradingSymbol":src.get("security_id"),"delta":self._num(g.get("delta")),"theta":self._num(g.get("theta")),"gamma":self._num(g.get("gamma")),"vega":self._num(g.get("vega"))}
            rows.append(row)
        rows.sort(key=lambda x:x["strike"])
        now=time.time(); self.last_update=now; self.last_error=None
        self.last_chain={"capturedAtEpoch":now,"capturedAt":datetime.fromtimestamp(now,timezone.utc).isoformat().replace("+00:00","Z"),"spot":self._num(data.get("last_price")),"expiry":expiry,"expiryDates":vals,"chain":rows,"source":"DHAN_OFFICIAL_API","dataQuality":"DhanHQ official option-chain API"}
        return self.last_chain
