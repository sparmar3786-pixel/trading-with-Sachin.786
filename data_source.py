from __future__ import annotations
import threading, time
from typing import Any
from .adapters.groww import GrowwAdapter
from .adapters.dhan import DhanAdapter

class OfficialDataSource:
    """Non-trading data source manager. Existing calculation engine remains untouched."""
    def __init__(self):
        self.provider='NONE'; self.adapter=None; self.connected=False; self.error=None; self.last_chain=None
        self._stop=threading.Event(); self._thread=None; self.interval=3.0

    def status(self):
        age=None
        if self.last_chain and self.last_chain.get('capturedAtEpoch'):
            age=max(0,time.time()-float(self.last_chain['capturedAtEpoch']))
        return {'provider':self.provider,'connected':self.connected,'error':self.error,'lastChainAgeSec':round(age,1) if age is not None else None,'source':self.last_chain.get('source') if self.last_chain else None}

    def connect(self, provider:str, **kwargs):
        self.disconnect(); p=provider.upper()
        if p=='GROWW': self.adapter=GrowwAdapter(kwargs.get('accessToken',''))
        elif p=='DHAN': self.adapter=DhanAdapter(kwargs.get('clientId',''),kwargs.get('accessToken',''),kwargs.get('underlyingIds'))
        else: raise ValueError('Unsupported official data provider.')
        self.provider=p; self.adapter.connect(); self.connected=True; self.error=None; self._stop.clear()
        self._thread=threading.Thread(target=self._poll_loop,daemon=True,name=f'{p.lower()}-option-chain')
        self._thread.start(); return self.status()

    def _poll_loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.last_chain=self.adapter.option_chain('NIFTY')
                self.connected=True; self.error=None
            except Exception as exc:
                self.connected=False; self.error=str(exc)

    def get_chain(self, underlying='NIFTY'):
        if not self.adapter: return None
        try:
            self.last_chain=self.adapter.option_chain(underlying); self.connected=True; self.error=None; return self.last_chain
        except Exception as exc:
            self.connected=False; self.error=str(exc); return self.last_chain

    def disconnect(self):
        self._stop.set(); self.connected=False; self.adapter=None; self.provider='NONE'; self.last_chain=None; self.error=None
