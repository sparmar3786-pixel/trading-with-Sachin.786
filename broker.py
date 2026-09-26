from __future__ import annotations
import asyncio, threading, time
from datetime import date, datetime
from typing import Any, Callable


LIVE_INDEX_CANDIDATES = {
    'NIFTY 50', 'NIFTY BANK', 'NIFTY FIN SERVICE', 'NIFTY IT', 'NIFTY AUTO',
    'NIFTY PHARMA', 'NIFTY FMCG', 'NIFTY METAL', 'NIFTY REALTY', 'NIFTY ENERGY',
    'NIFTY INFRA', 'NIFTY PSE', 'NIFTY PSU BANK', 'NIFTY PRIVATE BANK',
    'NIFTY MEDIA', 'NIFTY MNC', 'NIFTY COMMODITIES', 'NIFTY CONSUMPTION', 'INDIA VIX'
}


class BrokerManager:
    """Controlled broker session manager for live market-data collection.

    Secrets stay in process memory unless the caller explicitly asks the optional
    OS-keyring layer to remember them. Live ticks are buffered before persistence.
    The manager uses broker-supported reconnect/backoff plus an application watchdog.
    """
    def __init__(self, on_tick: Callable[[dict[str, Any]], Any]):
        self.on_tick = on_tick
        self.provider = 'none'
        self.api_key = ''
        self.access_token = ''
        self.connected = False
        self.error: str | None = None
        self.started_at: float | None = None
        self.last_tick: float | None = None
        self.instrument_count = 0
        self.symbols: list[str] = []
        self.reconnect_count = 0
        self.noreconnect_count = 0
        self.last_reconnect_at: float | None = None
        self.auth_error = False
        self.socket_count = 0
        self._tickers: list[Any] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._meta_by_token: dict[int, dict[str, Any]] = {}
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._token_groups: list[list[int]] = []
        self._lock = threading.Lock()

    def attach_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._event_loop = loop

    def status(self):
        age = None if self.last_tick is None else max(0.0, time.time() - self.last_tick)
        return {
            'provider': self.provider,
            'connected': self.connected,
            'error': self.error,
            'authError': self.auth_error,
            'startedAt': self.started_at,
            'lastTick': self.last_tick,
            'lastTickAgeSec': round(age, 1) if age is not None else None,
            'instrumentCount': self.instrument_count,
            'socketCount': self.socket_count,
            'symbols': self.symbols,
            'reconnectCount': self.reconnect_count,
            'noreconnectCount': self.noreconnect_count,
            'lastReconnectAt': self.last_reconnect_at,
        }

    def _run_callback(self, tick: dict[str, Any]):
        if not self._event_loop or self._event_loop.is_closed():
            try:
                asyncio.run(self.on_tick(tick))
            except Exception:
                pass
            return
        try:
            asyncio.run_coroutine_threadsafe(self.on_tick(tick), self._event_loop)
        except Exception:
            pass

    def _kite_instruments(self):
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=self.api_key)
        kite.set_access_token(self.access_token)
        nfo = kite.instruments('NFO')
        nse = kite.instruments('NSE')
        all_rows = nfo + nse
        today = date.today()
        wanted: list[dict[str, Any]] = []

        # Index spot values plus a broad set of major NSE indices for monitoring.
        for r in nse:
            ts = str(r.get('tradingsymbol', '')).strip()
            name = str(r.get('name', '')).strip()
            if r.get('segment') == 'NSE' and (ts in LIVE_INDEX_CANDIDATES or name in LIVE_INDEX_CANDIDATES):
                if str(r.get('instrument_type', '')).upper() == 'INDEX' or ts in {'NIFTY 50', 'NIFTY BANK', 'INDIA VIX'}:
                    wanted.append(r)

        option_rows = []
        for r in nfo:
            if r.get('instrument_type') not in {'CE', 'PE'}:
                continue
            name = str(r.get('name', '')).upper()
            if name not in {'NIFTY', 'BANKNIFTY'}:
                continue
            exp = r.get('expiry')
            if not exp:
                continue
            try:
                expd = exp.date() if hasattr(exp, 'date') and not isinstance(exp, date) else exp
                if expd < today:
                    continue
            except Exception:
                continue
            option_rows.append(r)

        expiries_by_name: dict[str, list[Any]] = {}
        for r in option_rows:
            expiries_by_name.setdefault(str(r.get('name')).upper(), []).append(r.get('expiry'))
        for name, vals in expiries_by_name.items():
            exps = sorted(set(vals))[:2]
            chosen = [r for r in option_rows if str(r.get('name')).upper() == name and r.get('expiry') in exps]
            wanted.extend(chosen)

        # Soft safety cap. We split across at most two sockets rather than truncating blindly.
        self._meta_by_token = {
            int(r['instrument_token']): r for r in wanted if r.get('instrument_token') is not None
        }
        tokens = list(self._meta_by_token.keys())
        self.instrument_count = len(tokens)
        self.symbols = sorted({str(r.get('name')) for r in wanted if r.get('name')})
        return wanted, tokens

    def _make_callbacks(self, ticker, tokens):
        def on_connect(ws, response):
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)
            with self._lock:
                self.connected = True
                self.started_at = self.started_at or time.time()
                self.error = None
                self.auth_error = False

        def on_ticks(ws, ticks):
            now = time.time()
            self.last_tick = now
            for t in ticks:
                token = int(t.get('instrument_token', -1)) if t.get('instrument_token') is not None else -1
                meta = self._meta_by_token.get(token, {})
                msg = {
                    'provider': 'ZERODHA',
                    'received_at': now,
                    'instrument_token': token,
                    'last_price': t.get('last_price'),
                    'volume': t.get('volume_traded'),
                    'oi': t.get('oi'),
                    'change': t.get('change'),
                    'ohlc': t.get('ohlc') or {},
                    'depth': t.get('depth') or {},
                    'exchange': meta.get('exchange'),
                    'segment': meta.get('segment'),
                    'tradingsymbol': meta.get('tradingsymbol'),
                    'name': meta.get('name'),
                    'strike': meta.get('strike'),
                    'expiry': str(meta.get('expiry')) if meta.get('expiry') else None,
                    'instrument_type': meta.get('instrument_type'),
                }
                self._run_callback(msg)

        def on_close(ws, code, reason):
            self.connected = any(getattr(t, 'is_connected', lambda: False)() for t in self._tickers if t is not ws)
            if not self._stop.is_set():
                self.error = f'WebSocket closed: {code} {reason}'

        def on_error(ws, code, reason):
            self.connected = False
            self.error = f'WebSocket error: {code} {reason}'
            text = str(reason).lower()
            if '403' in text or 'token' in text or 'unauthor' in text or 'permission' in text:
                self.auth_error = True

        def on_reconnect(ws, attempts_count):
            self.reconnect_count += 1
            self.last_reconnect_at = time.time()
            self.error = f'Reconnecting (attempt {attempts_count}; broker exponential backoff active)'

        def on_noreconnect(ws):
            self.noreconnect_count += 1
            self.connected = False
            self.error = 'Auto-reconnect exhausted; refresh the access token if authentication expired, otherwise reconnect manually.'

        ticker.on_connect = on_connect
        ticker.on_ticks = on_ticks
        ticker.on_close = on_close
        ticker.on_error = on_error
        # Supported by current Kite Connect Python client versions; callbacks are optional.
        if hasattr(ticker, 'on_reconnect'):
            ticker.on_reconnect = on_reconnect
        if hasattr(ticker, 'on_noreconnect'):
            ticker.on_noreconnect = on_noreconnect

    def _start_socket(self, tokens: list[int], socket_index: int):
        from kiteconnect import KiteTicker
        # Current Kite Connect clients expose exponential reconnect controls. The outer
        # watchdog below is still used to detect a silent/stalled connection.
        try:
            ticker = KiteTicker(
                self.api_key,
                self.access_token,
                reconnect=True,
                reconnect_max_tries=50,
                reconnect_max_delay=60,
            )
        except TypeError:
            ticker = KiteTicker(self.api_key, self.access_token)
        self._make_callbacks(ticker, tokens)
        self._tickers.append(ticker)

        def runner():
            try:
                ticker.connect(threaded=False)
            except Exception as exc:
                self.connected = False
                self.error = f'Socket {socket_index + 1} stopped: {exc}'
                if '403' in str(exc):
                    self.auth_error = True

        th = threading.Thread(target=runner, daemon=True, name=f'kite-ws-{socket_index + 1}')
        self._threads.append(th)
        th.start()

    def connect_zerodha(self, api_key: str, access_token: str):
        self.disconnect()
        self.provider = 'ZERODHA'
        self.api_key = api_key.strip()
        self.access_token = access_token.strip()
        self.error = None
        self.auth_error = False
        self.reconnect_count = 0
        self.noreconnect_count = 0
        if not self.api_key or not self.access_token:
            self.error = 'Zerodha API key and daily access token are required.'
            return self.status()
        try:
            _, tokens = self._kite_instruments()
            if not tokens:
                raise RuntimeError('No active NIFTY/BANKNIFTY/index instruments found.')
            max_per_socket = 2800
            self._token_groups = [tokens[i:i + max_per_socket] for i in range(0, len(tokens), max_per_socket)]
            if len(self._token_groups) > 3:
                raise RuntimeError('Instrument set exceeds the three-WebSocket safety limit. Narrow the watchlist.')
            self._stop.clear()
            self.started_at = time.time()
            for i, group in enumerate(self._token_groups):
                self._start_socket(group, i)
            self.socket_count = len(self._token_groups)
            return self.status()
        except Exception as exc:
            self.connected = False
            self.error = str(exc)
            self.socket_count = 0
            return self.status()

    def watchdog_reconnect(self, max_tick_age_sec: int = 60):
        if self._stop.is_set() or self.provider != 'ZERODHA' or not self._tickers:
            return False
        if self.auth_error:
            return False
        if self.last_tick is None or time.time() - self.last_tick <= max_tick_age_sec:
            return False
        self.error = f'Watchdog: no tick for >{max_tick_age_sec}s; rebuilding WebSocket session'
        key, token = self.api_key, self.access_token
        # Reinitialize ticker objects with the same token. Never auto-generate credentials.
        self._stop.set()
        for t in list(self._tickers):
            try:
                if hasattr(t, 'stop_retry'):
                    t.stop_retry()
                t.close()
            except Exception:
                pass
        self._tickers.clear()
        self._threads.clear()
        self._stop.clear()
        self.connect_zerodha(key, token)
        return True

    def disconnect(self):
        self._stop.set()
        for t in list(self._tickers):
            try:
                if hasattr(t, 'stop_retry'):
                    t.stop_retry()
                t.close()
            except Exception:
                pass
        self._tickers.clear()
        self._threads.clear()
        self.connected = False
        self.instrument_count = 0
        self.socket_count = 0
        self.symbols = []
        self._token_groups = []
        self._meta_by_token = {}


broker_manager = None
