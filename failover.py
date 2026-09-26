from __future__ import annotations
import time
import threading
from typing import Any
from .data_source import OfficialDataSource

class MarketDataFailover:
    """Read-only official-feed failover. A source switch locks signal eligibility
    until a fresh validated snapshot is received from the new source."""
    def __init__(self, source: OfficialDataSource):
        self.source = source
        self._lock = threading.Lock()
        self.priority: list[str] = []
        self.credentials: dict[str, dict[str, Any]] = {}
        self.active = 'NONE'
        self.previous = 'NONE'
        self.switch_count = 0
        self.last_switch = None
        self.switch_reason = None
        self.awaiting_fresh = False
        self.fresh_epoch = None
        self.last_validated_epoch = None
        self.last_error = None

    def configure(self, priority: list[str], credentials: dict[str, dict[str, Any]]):
        with self._lock:
            self.priority = [str(x).upper() for x in priority if str(x).strip()]
            self.credentials = {str(k).upper(): (v or {}) for k,v in credentials.items()}

    def _connect(self, provider: str):
        cfg = self.credentials.get(provider, {})
        if provider == 'GROWW':
            return self.source.connect('GROWW', accessToken=cfg.get('accessToken',''))
        if provider == 'DHAN':
            return self.source.connect('DHAN', clientId=cfg.get('clientId',''), accessToken=cfg.get('accessToken',''), underlyingIds=cfg.get('underlyingIds'))
        raise ValueError(f'Unsupported failover provider: {provider}')

    def switch_to(self, provider: str, reason: str):
        provider = provider.upper()
        result = self._connect(provider)
        with self._lock:
            self.previous = self.active
            self.active = provider
            self.switch_count += 1
            self.last_switch = time.time()
            self.switch_reason = reason
            self.awaiting_fresh = True
            self.fresh_epoch = None
            self.last_error = None
        return result

    def poll_once(self, underlying='NIFTY'):
        providers = self.priority or ([self.active] if self.active != 'NONE' else [])
        errors=[]
        for p in providers:
            if p != self.active:
                try:
                    self.switch_to(p, 'failover candidate')
                except Exception as exc:
                    errors.append(f'{p}: {exc}')
                    continue
            try:
                chain = self.source.get_chain(underlying)
                if not chain or not chain.get('chain'):
                    raise RuntimeError('empty option-chain')
                epoch = float(chain.get('capturedAtEpoch') or 0)
                with self._lock:
                    self.last_validated_epoch = epoch
                    if self.awaiting_fresh:
                        self.fresh_epoch = epoch
                        self.awaiting_fresh = False
                return chain
            except Exception as exc:
                errors.append(f'{p}: {exc}')
                with self._lock:
                    self.last_error = '; '.join(errors)
                continue
        return None

    def signal_gate(self):
        with self._lock:
            return {
                'eligible': bool(self.active != 'NONE' and not self.awaiting_fresh and self.last_validated_epoch),
                'reason': 'FRESH_DATA_REQUIRED_AFTER_SOURCE_SWITCH' if self.awaiting_fresh else (self.last_error or 'OK'),
                'activeSource': self.active,
                'switchCount': self.switch_count,
                'lastSwitchAt': self.last_switch,
                'lastValidatedEpoch': self.last_validated_epoch,
            }

    def status(self):
        with self._lock:
            return {**self.signal_gate(), 'previousSource': self.previous, 'switchReason': self.switch_reason, 'priority': self.priority}
