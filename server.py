from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any
import os
import json
import asyncio
import time
import threading
from collections import deque

try:
    from .storage import (
        save_snapshot, latest_snapshots, save_transition, save_transitions_batch,
        transition_stats, recent_ticks, tick_writer, retention_status, prune_old_data, save_signal_outcome, outcome_stats, save_regime, recent_regimes, recent_candles,
    )
    from .broker import BrokerManager
    from .data_source import OfficialDataSource
    from .failover import MarketDataFailover
    from . import broker_secrets as secret_store
except ImportError:
    from storage import (
        save_snapshot, latest_snapshots, save_transition, save_transitions_batch,
        transition_stats, recent_ticks, tick_writer, retention_status, prune_old_data, save_signal_outcome, outcome_stats, save_regime, recent_regimes, recent_candles,
    )
    from broker import BrokerManager
    from data_source import OfficialDataSource
    from failover import MarketDataFailover
    import broker_secrets as secret_store

from fastapi import FastAPI, Query, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import httpx

IST = ZoneInfo('Asia/Kolkata')
UTC = ZoneInfo('UTC')
NSE_BASE = 'https://www.nseindia.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
APP_ACCESS_TOKEN = os.getenv('APP_ACCESS_TOKEN', '').strip()
REQUIRE_HTTPS = os.getenv('REQUIRE_HTTPS', '0').strip().lower() in {'1', 'true', 'yes'}
TRUSTED_ORIGINS = [x.strip() for x in os.getenv('TRUSTED_ORIGINS', '').split(',') if x.strip()]
BROKER_SNAPSHOT_INTERVAL = max(5, int(os.getenv('BROKER_SNAPSHOT_INTERVAL_SEC', '30')))
BROKER_TICK_STALE_SEC = max(15, int(os.getenv('BROKER_TICK_STALE_SEC', '60')))
MAINTENANCE_INTERVAL = max(60, int(os.getenv('MAINTENANCE_INTERVAL_SEC', '900')))

app = FastAPI(title='NIFTY Option AI Data Gateway', version='2.2')
app.add_middleware(
    CORSMiddleware,
    allow_origins=TRUSTED_ORIGINS if TRUSTED_ORIGINS else ['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

broker_manager = BrokerManager(lambda tick: asyncio.sleep(0))
official_source = OfficialDataSource()
failover = MarketDataFailover(official_source)
_live_ticks: dict[int, dict[str, Any]] = {}
_live_ticks_lock = threading.Lock()
_background_tasks: list[asyncio.Task] = []
_last_broker_chain: dict[str, list[dict[str, Any]]] = {}
_active_signals: dict[str, dict[str, Any]] = {}
_ws_clients: set[WebSocket] = set()
_chart_state: dict[str, dict[str, Any]] = {}
_order_flow_state: dict[str, dict[str, Any]] = {}
_order_flow_history: dict[str, deque] = {}

def _chart_bucket(ts: float, seconds: int = 60) -> int:
    return int(ts // seconds) * seconds

def _update_chart(symbol: str, price: float | None, volume: float | None = None, oi: float | None = None, ts: float | None = None):
    if price is None: return None
    ts = ts or time.time(); bucket = _chart_bucket(ts, 60)
    c = _chart_state.get(symbol)
    if not c or c['bucket'] != bucket:
        c = {'bucket': bucket, 'open': price, 'high': price, 'low': price, 'close': price, 'volume': volume or 0, 'oi': oi, 'ts': ts}
        _chart_state[symbol] = c
    else:
        c['high'] = max(c['high'], price); c['low'] = min(c['low'], price); c['close'] = price
        if volume is not None: c['volume'] = volume
        if oi is not None: c['oi'] = oi
        c['ts'] = ts
    return dict(c)



def _update_order_flow(symbol: str, tick: dict[str, Any]):
    """Build an inferred order-flow series from live tick data.
    This is not exchange-level order-by-order tape unless full market-depth data is supplied.
    """
    price = parse_num(tick.get('last_price'))
    if price is None:
        return None
    ts = float(tick.get('received_at') or time.time())
    volume = parse_num(tick.get('volume'))
    oi = parse_num(tick.get('oi'))
    state = _order_flow_state.setdefault(symbol, {'price': None, 'volume': None, 'oi': None})
    prev_price, prev_volume, prev_oi = state['price'], state['volume'], state['oi']
    dv = 0.0 if volume is None or prev_volume is None else max(0.0, volume - prev_volume)
    dp = 0.0 if prev_price is None else price - prev_price
    bid = parse_num(tick.get('bid_price') or tick.get('bidprice'))
    ask = parse_num(tick.get('ask_price') or tick.get('askprice'))
    if bid is not None and ask is not None and ask > bid:
        mid = (bid + ask) / 2.0
        signed = 1 if price >= ask else (-1 if price <= bid else (1 if price >= mid else -1))
    else:
        signed = 1 if dp > 0 else (-1 if dp < 0 else 0)
    buy = dv if signed > 0 else 0.0
    sell = dv if signed < 0 else 0.0
    if dv == 0 and dp != 0:
        proxy = max(abs(dp) * max(abs(volume or 0), 1), 1.0)
        if signed > 0: buy = proxy
        else: sell = proxy
    bucket = _chart_bucket(ts, 60)
    hist = _order_flow_history.setdefault(symbol, deque(maxlen=180))
    if hist and hist[-1]['bucket'] == bucket:
        x = hist[-1]
        x['buy'] += buy; x['sell'] += sell; x['delta'] = x['buy'] - x['sell']; x['price'] = price; x['oi'] = oi
    else:
        x = {'bucket': bucket, 'ts': ts, 'price': price, 'oi': oi, 'buy': buy, 'sell': sell, 'delta': buy-sell}
        hist.append(x)
    state.update({'price': price, 'volume': volume, 'oi': oi})
    return dict(x)

async def _broadcast(message: dict[str, Any]):
    if not _ws_clients: return
    dead=[]
    for ws in list(_ws_clients):
        try: await ws.send_json(message)
        except Exception: dead.append(ws)
    for ws in dead: _ws_clients.discard(ws)
_metrics = {
    'brokerTicksReceived': 0,
    'brokerTickErrors': 0,
    'brokerSnapshots': 0,
    'brokerSnapshotSkips': 0,
    'maintenanceRuns': 0,
    'maintenanceErrors': 0,
    'lastMaintenance': None,
    'lastBrokerSnapshot': None,
    'lastBrokerSnapshotSymbols': [],
}


async def broker_tick_handler(tick: dict[str, Any]):
    from datetime import timezone
    try:
        tick['received_at_iso'] = datetime.fromtimestamp(tick.get('received_at', time.time()), timezone.utc).isoformat()
        token = int(tick.get('instrument_token'))
        with _live_ticks_lock:
            _live_ticks[token] = tick
        tick_writer.submit(tick)
        _metrics['brokerTicksReceived'] += 1
        symbol = str(tick.get('tradingsymbol') or tick.get('name') or '').upper()
        if symbol in {'NIFTY', 'NIFTY 50', 'NIFTY50', 'NIFTY 50.0'} or 'NIFTY' in symbol and str(tick.get('instrument_type') or '').upper() == 'INDEX':
            candle = _update_chart('NIFTY', parse_num(tick.get('last_price')), parse_num(tick.get('volume')), parse_num(tick.get('oi')), tick.get('received_at'))
            flow = _update_order_flow('NIFTY', tick)
            await _broadcast({'type':'tick','symbol':'NIFTY','tick':tick,'candle':candle,'orderFlow':flow})
    except Exception:
        _metrics['brokerTickErrors'] += 1


broker_manager.on_tick = broker_tick_handler


@app.middleware('http')
async def security_middleware(request: Request, call_next):
    # Health/clock are intentionally public so external uptime monitors can work.
    public_paths = {'/health', '/clock'}
    if REQUIRE_HTTPS and request.url.path not in public_paths:
        forwarded = request.headers.get('x-forwarded-proto', '').split(',')[0].strip().lower()
        scheme = forwarded or request.url.scheme
        if scheme != 'https':
            return await _json_response({'detail': 'HTTPS is required in production mode.'}, 400)
    if APP_ACCESS_TOKEN and request.url.path not in public_paths:
        supplied = request.headers.get('x-app-token', '')
        if supplied != APP_ACCESS_TOKEN:
            return await _json_response({'detail': 'Missing or invalid app access token.'}, 401)
    return await call_next(request)


async def _json_response(payload: dict[str, Any], status_code: int):
    from fastapi.responses import JSONResponse
    return JSONResponse(payload, status_code=status_code)


def nse_headers() -> dict[str, str]:
    return {
        'User-Agent': UA,
        'Accept': 'application/json,text/plain,*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': f'{NSE_BASE}/option-chain',
        'Origin': NSE_BASE,
        'Connection': 'keep-alive',
    }


async def nse_get(path: str, params: dict[str, Any] | None = None) -> Any:
    timeout = httpx.Timeout(15.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, headers=nse_headers(), follow_redirects=True) as client:
        await client.get(f'{NSE_BASE}/option-chain')
        response = await client.get(f'{NSE_BASE}{path}', params=params)
        response.raise_for_status()
        return response.json()


def parse_num(v: Any) -> float | None:
    if v in (None, '', '-'):
        return None
    try:
        return float(str(v).replace(',', ''))
    except Exception:
        return None


def parse_nse_time(v: Any) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ('%d-%b-%Y %H:%M:%S', '%d-%b-%Y %H:%M'):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=IST)
            return dt.astimezone(UTC).isoformat().replace('+00:00', 'Z')
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(UTC).isoformat().replace('+00:00', 'Z')
    except Exception:
        return None


def normalize_option_chain(payload: dict[str, Any]) -> dict[str, Any]:
    records = payload.get('records') or {}
    rows: list[dict[str, Any]] = []
    for item in records.get('data') or []:
        strike = parse_num(item.get('strikePrice'))
        if strike is None:
            continue
        ce_raw = item.get('CE') or {}
        pe_raw = item.get('PE') or {}
        rows.append({
            'strike': strike,
            'ce': {
                'oi': parse_num(ce_raw.get('openInterest')),
                'doi': parse_num(ce_raw.get('changeinOpenInterest')),
                'vol': parse_num(ce_raw.get('totalTradedVolume')),
                'iv': parse_num(ce_raw.get('impliedVolatility')),
                'ltp': parse_num(ce_raw.get('lastPrice')),
                'chg': parse_num(ce_raw.get('change')),
                'bid': parse_num(ce_raw.get('bidprice')),
                'ask': parse_num(ce_raw.get('askPrice')),
            },
            'pe': {
                'bid': parse_num(pe_raw.get('bidprice')),
                'ask': parse_num(pe_raw.get('askPrice')),
                'chg': parse_num(pe_raw.get('change')),
                'ltp': parse_num(pe_raw.get('lastPrice')),
                'iv': parse_num(pe_raw.get('impliedVolatility')),
                'vol': parse_num(pe_raw.get('totalTradedVolume')),
                'doi': parse_num(pe_raw.get('changeinOpenInterest')),
                'oi': parse_num(pe_raw.get('openInterest')),
            },
        })
    rows.sort(key=lambda x: x['strike'])
    return {
        'capturedAt': parse_nse_time(records.get('timestamp')) or records.get('timestamp'),
        'capturedAtRaw': records.get('timestamp'),
        'spot': parse_num(records.get('underlyingValue')),
        'expiry': (records.get('expiryDates') or [None])[0],
        'expiryDates': records.get('expiryDates') or [],
        'chain': rows,
        'dataQuality': 'NSE official option-chain snapshot fields',
    }


def normalize_history_rows(payload: Any) -> list[dict[str, Any]]:
    data = []
    if isinstance(payload, dict):
        if isinstance(payload.get('data'), list):
            data = payload['data']
        elif isinstance(payload.get('records'), dict) and isinstance(payload['records'].get('data'), list):
            data = payload['records']['data']
    elif isinstance(payload, list):
        data = payload
    rows: list[dict[str, Any]] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        ts = r.get('CH_TIMESTAMP') or r.get('TIMESTAMP') or r.get('date') or r.get('Date')
        dt = None
        for fmt in ('%d-%b-%Y', '%d-%m-%Y', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(str(ts), fmt).date()
                break
            except Exception:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00')).date()
            except Exception:
                continue
        rows.append({
            'date': dt.isoformat(),
            'open': parse_num(r.get('CH_OPENING_PRICE') or r.get('OPEN_INDEX_VAL') or r.get('Open') or r.get('open')),
            'high': parse_num(r.get('CH_TRADE_HIGH_PRICE') or r.get('HIGH_INDEX_VAL') or r.get('High') or r.get('high')),
            'low': parse_num(r.get('CH_TRADE_LOW_PRICE') or r.get('LOW_INDEX_VAL') or r.get('Low') or r.get('low')),
            'close': parse_num(r.get('CH_CLOSING_PRICE') or r.get('CLOSING_INDEX_VAL') or r.get('Close') or r.get('close')),
        })
    rows.sort(key=lambda x: x['date'])
    return rows


async def fetch_index_history(index_type: str, start: date, end: date) -> list[dict[str, Any]]:
    params = {'indexType': index_type, 'from': start.strftime('%d-%m-%Y'), 'to': end.strftime('%d-%m-%Y')}
    payload = await nse_get('/api/historical/indicesHistory', params=params)
    rows = normalize_history_rows(payload)
    if not rows:
        raise RuntimeError('NSE historical index API returned no parsable rows')
    return rows


def prev_week_bounds(asof: date) -> tuple[date, date]:
    monday = asof - timedelta(days=asof.weekday())
    return monday - timedelta(days=7), monday - timedelta(days=1)


def prev_month_bounds(asof: date) -> tuple[date, date]:
    first = asof.replace(day=1)
    last_prev = first - timedelta(days=1)
    return last_prev.replace(day=1), last_prev


async def build_reference_context(symbol: str, asof: date) -> dict[str, Any]:
    periods: dict[str, dict[str, Any]] = {}
    one_day_rows = await fetch_index_history(symbol, asof - timedelta(days=10), asof - timedelta(days=1))
    if one_day_rows:
        periods['1D'] = {**one_day_rows[-1], 'time': 'EOD', 'source': 'NSE Historical Index Data'}
    ws, we = prev_week_bounds(asof)
    week_rows = await fetch_index_history(symbol, ws, we)
    if week_rows:
        periods['1W'] = {
            'date': f"{week_rows[0]['date']} → {week_rows[-1]['date']}",
            'time': 'Previous completed week',
            'high': max(x['high'] for x in week_rows if x['high'] is not None),
            'low': min(x['low'] for x in week_rows if x['low'] is not None),
            'close': week_rows[-1]['close'],
            'source': 'NSE Historical Index Data',
        }
    ms, me = prev_month_bounds(asof)
    month_rows = await fetch_index_history(symbol, ms, me)
    if month_rows:
        periods['1M'] = {
            'date': f"{month_rows[0]['date']} → {month_rows[-1]['date']}",
            'time': 'Previous completed month',
            'high': max(x['high'] for x in month_rows if x['high'] is not None),
            'low': min(x['low'] for x in month_rows if x['low'] is not None),
            'close': month_rows[-1]['close'],
            'source': 'NSE Historical Index Data',
        }
    return periods


INDEX_SYMBOLS = [
    'NIFTY 50', 'NIFTY BANK', 'NIFTY FINANCIAL SERVICES', 'NIFTY MIDCAP 50', 'NIFTY NEXT 50',
    'NIFTY IT', 'NIFTY AUTO', 'NIFTY PHARMA', 'NIFTY FMCG', 'NIFTY METAL', 'NIFTY REALTY',
    'NIFTY ENERGY', 'NIFTY INFRA', 'NIFTY PSE', 'NIFTY PSU BANK', 'NIFTY PRIVATE BANK',
    'NIFTY MEDIA', 'NIFTY MNC', 'NIFTY COMMODITIES', 'NIFTY CONSUMPTION', 'NIFTY INDIA VIX'
]


def normalize_index_rows(payload: Any) -> list[dict[str, Any]]:
    data = payload.get('data', []) if isinstance(payload, dict) else []
    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        name = r.get('index') or r.get('indexSymbol') or r.get('name')
        ltp = parse_num(r.get('last') or r.get('lastPrice') or r.get('ltp'))
        chg = parse_num(r.get('variation') or r.get('change'))
        pchg = parse_num(r.get('percentChange') or r.get('percent_change'))
        out.append({
            'name': name, 'ltp': ltp, 'change': chg, 'percentChange': pchg,
            'open': parse_num(r.get('open')), 'high': parse_num(r.get('high')),
            'low': parse_num(r.get('low')), 'previousClose': parse_num(r.get('previousClose')),
            'updatedAt': r.get('lastUpdateTime') or r.get('updatedAt'),
        })
    return out


@app.get('/proxy/nse-all-indices')
async def nse_all_indices():
    payload = await nse_get('/api/allIndices')
    rows = normalize_index_rows(payload)
    return {'data': rows, 'capturedAt': datetime.now(IST).isoformat(), 'source': 'NSE'}


def validate_market_snapshot(chain: list[dict[str, Any]], spot: float | None, captured_at: str | None, source: str | None, max_age_sec: int = 300) -> dict[str, Any]:
    now = datetime.now(UTC)
    reasons=[]
    age=None
    if not captured_at:
        reasons.append('missing capture timestamp')
    else:
        try:
            dt=datetime.fromisoformat(str(captured_at).replace('Z','+00:00'))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=UTC)
            age=max(0,(now-dt.astimezone(UTC)).total_seconds())
            if age>max_age_sec: reasons.append(f'stale snapshot: {int(age)}s')
            if age< -10: reasons.append('future timestamp / clock mismatch')
        except Exception: reasons.append('invalid capture timestamp')
    strikes=[r.get('strike') for r in chain if r.get('strike') is not None]
    complete=len(chain)>=5 and len(strikes)==len(set(strikes))
    if not complete: reasons.append('option-chain incomplete or duplicate strikes')
    if spot is None or spot<=0: reasons.append('missing/invalid spot')
    numeric_ok=sum(1 for r in chain for side in ('ce','pe') if (r.get(side) or {}).get('ltp') is not None)
    if chain and numeric_ok < max(4, len(chain)//2): reasons.append('too many missing option LTP values')
    return {'ok':not reasons,'ageSec':None if age is None else round(age,1),'source':source or 'unknown','reasons':reasons,'checks':{'timestamp':bool(captured_at),'fresh':age is not None and 0<=age<=max_age_sec,'complete':complete,'spot':spot is not None and spot>0,'optionLtpCoverage':numeric_ok}}


def signal_state(current: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if not current.get('dataValid', False):
        return {'state':'WAIT','reason':'Data validation failed; no trade signal released.'}
    bull=float(current.get('bullish') or 0); bear=float(current.get('bearish') or 0)
    ce_override=bool(current.get('ceOverride')); pe_override=bool(current.get('peOverride'))
    if ce_override or pe_override: return {'state':'WATCH','reason':'Opposite-side short-covering override is active.'}
    edge=max(bull,bear)
    if edge<45: return {'state':'WAIT','reason':'No confirmed directional edge.'}
    if not current.get('barsReady',False): return {'state':'WATCH','reason':'Intraday bars are insufficient for high-confidence entry.'}
    if abs(bull-bear)<10: return {'state':'WATCH','reason':'Bull/bear structure is too close; waiting for confirmation.'}
    return {'state':'CONFIRMED','reason':'Fresh data + bars + directional structure confirmed.'}

def market_regime(chain: list[dict[str, Any]], spot: float | None) -> dict[str, Any]:
    if not chain or spot is None:
        return {'regime':'UNKNOWN','score':0,'metrics':{}}
    near=sorted(chain,key=lambda r:abs((r.get('strike') or spot)-spot))[:9]
    ce_cover=pe_cover=ce_write=pe_write=0
    for r in near:
        for side in ('ce','pe'):
            x=r.get(side) or {}
            if (x.get('ltp') or 0)>0 and (x.get('doi') or 0)<0:
                if side=='ce': ce_cover+=1
                else: pe_cover+=1
            if (x.get('doi') or 0)>0 and (x.get('chg') or 0)<0:
                if side=='ce': ce_write+=1
                else: pe_write+=1
    metrics={'ceCover':ce_cover,'peCover':pe_cover,'ceWrite':ce_write,'peWrite':pe_write,'nearStrikes':len(near)}
    if ce_cover>=2 and ce_cover>pe_cover+1: regime='CALL_COVERING'
    elif pe_cover>=2 and pe_cover>ce_cover+1: regime='PUT_COVERING'
    elif ce_write>=2 and pe_write>=2: regime='RANGE/WRITING'
    elif ce_cover>=2 and pe_cover>=2: regime='TWO_SIDED_EXPANSION'
    else: regime='NEUTRAL/TRANSITION'
    score=min(100,abs(ce_cover-pe_cover)*25 + max(ce_write,pe_write)*10)
    return {'regime':regime,'score':score,'metrics':metrics}


def similar_transition_memory(symbol: str, side: str, strike: float, limit: int = 10):
    import sqlite3
    try:
        from .storage import DB_PATH
    except ImportError:
        from storage import DB_PATH
    with sqlite3.connect(str(DB_PATH)) as db:
        db.row_factory=sqlite3.Row
        rows=db.execute("SELECT stage,score,premium_change_pct,oi_change_pct,volume_change_pct,created_at FROM transitions t JOIN snapshots s ON s.id=t.snapshot_id WHERE s.symbol=? AND t.side=? ORDER BY ABS(t.strike-?) ASC, t.id DESC LIMIT ?",(symbol,side,strike,limit)).fetchall()
    return [dict(r) for r in rows]


@app.post('/intelligence/evaluate')
async def intelligence_evaluate(payload: dict[str, Any]):
    chain=payload.get('chain') or []; spot=parse_num(payload.get('spot')); symbol=str(payload.get('symbol') or 'NIFTY')
    gate=failover.signal_gate()
    if failover.priority and not gate['eligible']:
        return {'status':'WAIT','reason':gate['reason'],'failover':gate,'regime':{'regime':'WAIT','score':0,'metrics':{}},'similarMemory':{'ce':[],'pe':[]}}
    reg=market_regime(chain,spot)
    if chain:
        save_regime(symbol, datetime.now(UTC).isoformat().replace('+00:00','Z'), reg['regime'], reg['score'], reg['metrics'])
    mid=float(chain[len(chain)//2].get('strike') or 0) if chain else 0
    return {'regime':reg,'similarMemory':{'ce':similar_transition_memory(symbol,'ce',mid) if chain else [],'pe':similar_transition_memory(symbol,'pe',mid) if chain else []}}

@app.post('/outcomes/record')
async def record_outcome(payload: dict[str, Any]):
    required=['symbol','side','strike','outcome']
    if any(payload.get(k) in (None,'') for k in required): raise HTTPException(status_code=400,detail='symbol, side, strike and outcome are required')
    oid=save_signal_outcome(payload)
    return {'id':oid,'stats':outcome_stats(str(payload['symbol']))}

@app.get('/outcomes/stats')
async def get_outcome_stats(symbol: str='NIFTY'):
    return outcome_stats(symbol)

@app.get('/journal/recent')
async def journal_recent(symbol: str='NIFTY', limit: int=50):
    import sqlite3
    try:
        from .storage import DB_PATH
    except ImportError:
        from storage import DB_PATH
    with sqlite3.connect(str(DB_PATH)) as db:
        db.row_factory=sqlite3.Row
        rows=db.execute('SELECT * FROM signal_outcomes WHERE symbol=? ORDER BY id DESC LIMIT ?', (symbol, max(1,min(limit,500)))).fetchall()
    return {'symbol':symbol,'trades':[dict(r) for r in rows]}


@app.get('/intelligence/regime')
async def get_regime(symbol: str='NIFTY'):
    return {'symbol':symbol,'history':recent_regimes(symbol,30)}

@app.get('/candles')
async def get_candles(symbol: str='NIFTY', timeframe: str='1m', limit: int=100):
    return {'symbol':symbol,'timeframe':timeframe,'data':recent_candles(symbol,timeframe,max(1,min(limit,1000)))}

@app.get('/replay/run')
async def replay_run(symbol: str='NIFTY', limit: int=5000):
    rows=historical_replay(symbol,max(3,min(limit,5000)))
    counts={}
    for r in rows: counts[r['outcome']]=counts.get(r['outcome'],0)+1
    return {'symbol':symbol,'episodes':rows[:500],'totalEpisodes':len(rows),'outcomeCounts':counts,'note':'Replay uses the next stored snapshot after a Stage-C transition. It is descriptive backtesting, not a forecast.'}

@app.get('/replay/summary')
async def replay_summary(symbol: str='NIFTY'):
    return {'symbol':symbol,'transitionStats':transition_stats(symbol),'outcomes':outcome_stats(symbol),'note':'Historical replay statistics are descriptive; no future outcome is guaranteed.'}

@app.post('/ai/audit')
async def ai_audit(payload: dict[str, Any]):
    chain = payload.get('chain') or []
    spot = parse_num(payload.get('spot'))
    question = str(payload.get('question') or 'Audit current market')
    fresh = bool(payload.get('fresh'))
    bars = int(payload.get('bars') or 0)
    ce_cover, pe_cover = [], []
    for r in chain:
        for side, arr in (('CE', ce_cover), ('PE', pe_cover)):
            x = r.get(side.lower()) or {}
            if parse_num(x.get('ltp')) is not None and parse_num(x.get('doi')) is not None:
                if (parse_num(x.get('ltp')) or 0) >= 0 and (parse_num(x.get('doi')) or 0) < 0 and (parse_num(x.get('chg')) or 0) > 0:
                    arr.append(r.get('strike'))
    ceoi = sum((parse_num((r.get('ce') or {}).get('oi')) or 0) for r in chain)
    peoi = sum((parse_num((r.get('pe') or {}).get('oi')) or 0) for r in chain)
    pcr_oi = peoi / ceoi if ceoi else None
    blockers = []
    if not fresh:
        blockers.append('Freshness gate failed: current-trade signal is blocked.')
    if bars <= 0:
        blockers.append('No verified intraday bars: high-confidence intraday signal is blocked.')
    if not chain:
        blockers.append('No option-chain snapshot available.')
    view = 'NEUTRAL / WAIT'
    if ce_cover and not pe_cover:
        view = 'CALL SHORT-COVERING WATCH'
    elif pe_cover and not ce_cover:
        view = 'PUT SHORT-COVERING WATCH'
    elif ce_cover and pe_cover:
        view = 'TWO-SIDED EXPANSION / EVENT WATCH'
    response = (
        f'AI Audit for: {question}\n'
        f'Spot: {spot if spot is not None else "n/a"} | PCR(OI): {round(pcr_oi, 2) if pcr_oi is not None else "n/a"}\n'
        f'Market structure: {view}. CE short-cover candidates: {", ".join(map(str, ce_cover[:6])) or "none"}; '
        f'PE short-cover candidates: {", ".join(map(str, pe_cover[:6])) or "none"}.\n'
        + ('BLOCKERS: ' + ' '.join(blockers) if blockers else 'Gate status: data is eligible for rule-based review; setup still requires confirmation.')
        + '\nThe audit reports observable evidence; it does not claim hidden buyer/seller identities or exact private stop-loss orders.'
    )
    return {'answer': response, 'view': view, 'blockers': blockers, 'source': 'Local AI Audit Engine', 'timeIST': datetime.now(IST).isoformat()}


def _pct(a, b):
    if a is None or b in (None, 0):
        return None
    return ((a - b) / abs(b)) * 100.0


def _transition(prev_row, row, side):
    x = row.get(side) or {}
    p = prev_row.get(side) or {}
    ltp, pltp = x.get('ltp'), p.get('ltp')
    oi, poi = x.get('oi'), p.get('oi')
    vol, pvol = x.get('vol'), p.get('vol')
    pc, oc, vc = _pct(ltp, pltp), _pct(oi, poi), _pct(vol, pvol)
    reasons, score = [], 0
    if pc is not None and pc > 0:
        score += 35; reasons.append('premium rising')
    if oc is not None and oc < 0:
        score += 35; reasons.append('OI falling')
    if vc is not None and vc > 0:
        score += 20; reasons.append('volume expanding')
    if pc is not None and pc < 0:
        score -= 20; reasons.append('premium falling')
    if oc is not None and oc > 0:
        score -= 15; reasons.append('OI rising')
    stage = 'A'
    if score >= 35:
        stage = 'B'
    if score >= 70 and pc is not None and pc > 0 and oc is not None and oc < 0:
        stage = 'C'
    return stage, max(0, min(100, score)), pc, oc, vc, reasons


def store_snapshot_and_transitions(chain: list[dict[str, Any]], symbol: str, captured: str, source: str, spot: float | None, expiry: str | None) -> dict[str, Any]:
    snaps = latest_snapshots(symbol, 1)
    sid = save_snapshot(captured, source, symbol, spot, expiry, chain)
    prev_id = snaps[0]['id'] if snaps else None
    prev = json.loads(snaps[0]['chain_json']) if snaps else []
    prev_map = {float(r.get('strike')): r for r in prev if r.get('strike') is not None}
    items = []
    if prev:
        for r in chain:
            strike = r.get('strike')
            pr = prev_map.get(float(strike)) if strike is not None else None
            if not pr:
                continue
            for side in ('ce', 'pe'):
                stage, score, pc, oc, vc, reasons = _transition(pr, r, side)
                items.append({
                    'snapshot_id': sid, 'previous_snapshot_id': prev_id, 'side': side,
                    'strike': float(strike), 'stage': stage, 'score': score,
                    'premium_change_pct': pc, 'oi_change_pct': oc, 'volume_change_pct': vc,
                    'reasons': reasons,
                })
        save_transitions_batch(items)
    return {'snapshotId': sid, 'previousSnapshotId': prev_id, 'transitionsRecorded': len(items)}


def _tick_to_option_row(tick: dict[str, Any]) -> tuple[str, float, str, dict[str, Any]] | None:
    typ = str(tick.get('instrument_type') or '').upper()
    if typ not in {'CE', 'PE'}:
        return None
    name = str(tick.get('name') or '').upper()
    if name not in {'NIFTY', 'BANKNIFTY'}:
        return None
    strike = parse_num(tick.get('strike'))
    if strike is None:
        return None
    return name, strike, typ, tick


def build_broker_chain(underlying: str) -> tuple[list[dict[str, Any]], float | None, str | None] | None:
    official = official_source.get_chain(underlying) if official_source.connected else None
    if official and official.get('chain'):
        return official.get('chain') or [], official.get('spot'), official.get('expiry')
    with _live_ticks_lock:
        ticks = list(_live_ticks.values())
    options = [x for x in ticks if _tick_to_option_row(x) and _tick_to_option_row(x)[0] == underlying]
    if not options:
        return None
    expiries = sorted({str(x.get('expiry')) for x in options if x.get('expiry')})
    expiry = expiries[0] if expiries else None
    selected = [x for x in options if (not expiry or str(x.get('expiry')) == expiry)]
    by_strike: dict[float, dict[str, Any]] = {}
    for tick in selected:
        strike = float(tick.get('strike'))
        row = by_strike.setdefault(strike, {'strike': strike, 'ce': {}, 'pe': {}})
        typ = str(tick.get('instrument_type')).lower()
        ltp = parse_num(tick.get('last_price'))
        ohlc = tick.get('ohlc') or {}
        close = parse_num(ohlc.get('close'))
        change = (ltp - close) if ltp is not None and close is not None else None
        depth = tick.get('depth') or {}
        buys = depth.get('buy') or []
        sells = depth.get('sell') or []
        row[typ] = {
            'oi': parse_num(tick.get('oi')),
            'doi': None,  # computed as interval OI change below; not NSE's official day-change field
            'vol': parse_num(tick.get('volume')),
            'iv': None,
            'ltp': ltp,
            'chg': change,
            'bid': parse_num(buys[0].get('price')) if buys else None,
            'ask': parse_num(sells[0].get('price')) if sells else None,
        }
    chain = sorted(by_strike.values(), key=lambda x: x['strike'])
    prev = _last_broker_chain.get(underlying, [])
    prev_map = {float(r['strike']): r for r in prev}
    for row in chain:
        pr = prev_map.get(float(row['strike']))
        for side in ('ce', 'pe'):
            oi = parse_num((row.get(side) or {}).get('oi'))
            poi = parse_num(((pr or {}).get(side) or {}).get('oi'))
            row[side]['doi'] = (oi - poi) if oi is not None and poi is not None else None
            row[side]['doiSource'] = 'broker_interval_snapshot_delta'
    _last_broker_chain[underlying] = json.loads(json.dumps(chain))
    index_name = 'NIFTY 50' if underlying == 'NIFTY' else 'NIFTY BANK'
    with _live_ticks_lock:
        candidates = [t for t in _live_ticks.values() if str(t.get('name') or '').upper() in {underlying, index_name}]
    spot = parse_num(candidates[-1].get('last_price')) if candidates else None
    captured = datetime.now(UTC).isoformat().replace('+00:00', 'Z')
    return chain, spot, expiry


def evaluate_auto_outcomes(symbol: str, chain: list[dict[str, Any]], snapshot_id: int):
    by={float(r['strike']):r for r in chain if r.get('strike') is not None}
    done=[]
    for key, sig in list(_active_signals.items()):
        if sig['symbol']!=symbol: continue
        r=by.get(float(sig['strike']));
        if not r: continue
        x=r.get(sig['side']) or {}; ltp=parse_num(x.get('ltp'))
        if ltp is None: continue
        entry=sig['entry']; outcome=None
        if ltp <= sig['sl']: outcome='SL'
        elif ltp >= sig['t2']: outcome='T2'
        elif ltp >= sig['t1']: outcome='T1'
        sig['bars'] += 1
        sig['max_fav']=max(sig['max_fav'], ((ltp-entry)/entry)*100 if entry else 0)
        sig['max_adv']=min(sig['max_adv'], ((ltp-entry)/entry)*100 if entry else 0)
        if outcome or sig['bars']>=20:
            outcome=outcome or 'TIMEOUT'
            save_signal_outcome({'symbol':symbol,'side':sig['side'],'strike':sig['strike'],'signal_stage':sig['stage'],'entry':entry,'stop_loss':sig['sl'],'target1':sig['t1'],'target2':sig['t2'],'exit_price':ltp,'outcome':outcome,'bars_held':sig['bars'],'max_favorable_pct':sig['max_fav'],'max_adverse_pct':sig['max_adv'],'context':{'snapshotId':snapshot_id,'source':sig.get('source')}})
            done.append(key)
    for key in done: _active_signals.pop(key,None)
    # Start at most one nearest-ATM C-stage candidate per side/strike.
    if not chain: return
    spot_vals=[r.get('strike') for r in chain if r.get('strike') is not None]
    if not spot_vals: return
    mid=spot_vals[len(spot_vals)//2]
    for side in ('ce','pe'):
        candidates=[]
        for r in chain:
            x=r.get(side) or {}
            if x.get('ltp') is None or x.get('doi') is None: continue
            # Current official/derived change fields are combined with stored transition evidence.
            if (x.get('chg') or 0)>0 and (x.get('doi') or 0)<0:
                candidates.append(r)
        if not candidates: continue
        r=min(candidates,key=lambda z:abs(float(z['strike'])-float(mid)))
        tkey=f'{symbol}:{side}:{float(r["strike"])}'
        if tkey in _active_signals: continue
        entry=float((r.get(side) or {}).get('ltp') or 0)
        if entry<=0: continue
        _active_signals[tkey]={'symbol':symbol,'side':side,'strike':float(r['strike']),'entry':entry,'sl':entry*0.92,'t1':entry*1.12,'t2':entry*1.25,'stage':'C? / live candidate','bars':0,'max_fav':0.0,'max_adv':0.0,'source':'automatic candidate monitor'}


def historical_replay(symbol: str='NIFTY', limit: int=5000):
    snaps=latest_snapshots(symbol,limit); snaps=list(reversed(snaps))
    results=[]
    for i in range(1,len(snaps)-1):
        prev=json.loads(snaps[i-1]['chain_json']); cur=json.loads(snaps[i]['chain_json']); nxt=json.loads(snaps[i+1]['chain_json'])
        pm={float(r['strike']):r for r in prev if r.get('strike') is not None}; nm={float(r['strike']):r for r in nxt if r.get('strike') is not None}
        for r in cur:
            strike=r.get('strike');
            if strike is None or float(strike) not in pm or float(strike) not in nm: continue
            for side in ('ce','pe'):
                stage,score,pc,oc,vc,reasons=_transition(pm[float(strike)],r,side)
                if stage!='C': continue
                entry=parse_num((r.get(side) or {}).get('ltp')); future=parse_num((nm[float(strike)].get(side) or {}).get('ltp'))
                if not entry or future is None: continue
                move=(future-entry)/entry*100
                outcome='TARGET_DIRECTION' if move>12 else ('ADVERSE' if move<-8 else 'UNRESOLVED')
                results.append({'side':side,'strike':strike,'stage':stage,'score':score,'premiumChangePct':pc,'oiChangePct':oc,'volumeChangePct':vc,'nextMovePct':move,'outcome':outcome,'at':snaps[i]['captured_at']})
    return results


async def broker_snapshot_loop():
    while True:
        try:
            if broker_manager.connected:
                recorded = []
                for symbol in ('NIFTY', 'BANKNIFTY'):
                    built = build_broker_chain(symbol)
                    if not built:
                        continue
                    chain, spot, expiry = built
                    if len(chain) < 5:
                        continue
                    result = store_snapshot_and_transitions(
                        chain, symbol, datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
                        'BROKER_WS', spot, expiry
                    )
                    recorded.append(symbol)
                    _metrics['brokerSnapshots'] += 1
                if recorded:
                    _metrics['lastBrokerSnapshot'] = time.time()
                    _metrics['lastBrokerSnapshotSymbols'] = recorded
                else:
                    _metrics['brokerSnapshotSkips'] += 1
        except Exception:
            _metrics['brokerSnapshotSkips'] += 1
        await asyncio.sleep(BROKER_SNAPSHOT_INTERVAL)


async def maintenance_loop():
    while True:
        await asyncio.sleep(MAINTENANCE_INTERVAL)
        try:
            result = prune_old_data(run_vacuum=False)
            _metrics['maintenanceRuns'] += 1
            _metrics['lastMaintenance'] = result
        except Exception as exc:
            _metrics['maintenanceErrors'] += 1
            _metrics['lastMaintenance'] = {'error': str(exc)}


async def failover_loop():
    while True:
        try:
            if failover.priority and official_source.connected:
                chain = failover.poll_once('NIFTY')
                if chain:
                    _metrics['lastBrokerSnapshot'] = time.time()
                    await _broadcast({'type':'source_status','failover':failover.status()})
        except Exception:
            pass
        await asyncio.sleep(max(10, BROKER_SNAPSHOT_INTERVAL))


async def watchdog_loop():
    while True:
        try:
            if broker_manager.provider == 'ZERODHA' and broker_manager.connected:
                broker_manager.watchdog_reconnect(BROKER_TICK_STALE_SEC)
        except Exception:
            pass
        await asyncio.sleep(15)


@app.post('/data/validate')
async def data_validate(payload: dict[str, Any]):
    return validate_market_snapshot(payload.get('chain') or [], parse_num(payload.get('spot')), payload.get('capturedAt'), payload.get('source'), int(payload.get('maxAgeSec') or 300))

@app.post('/signal/state')
async def signal_state_api(payload: dict[str, Any]):
    return signal_state(payload, payload.get('previous'))

@app.get('/health/extended')
async def extended_health():
    b=broker_manager.status()
    return {'status':'ok','serverTime':datetime.now(IST).isoformat(),'wsClients':len(_ws_clients),'broker':b,'officialFailover':failover.status(),'metrics':_metrics,'memory':retention_status()}

@app.on_event('startup')
async def startup_event():
    loop = asyncio.get_running_loop()
    broker_manager.attach_event_loop(loop)
    tick_writer.start()
    _background_tasks.extend([
        asyncio.create_task(broker_snapshot_loop(), name='broker-snapshot-loop'),
        asyncio.create_task(maintenance_loop(), name='db-maintenance-loop'),
        asyncio.create_task(watchdog_loop(), name='broker-watchdog-loop'),
        asyncio.create_task(failover_loop(), name='official-feed-failover-loop'),
    ])


@app.on_event('shutdown')
async def shutdown_event():
    broker_manager.disconnect()
    official_source.disconnect()
    tick_writer.stop()
    for task in _background_tasks:
        task.cancel()


@app.post('/memory/snapshot')
async def memory_snapshot(payload: dict[str, Any]):
    chain = payload.get('chain') or []
    symbol = str(payload.get('symbol') or 'NIFTY')
    captured = str(payload.get('capturedAt') or datetime.now(IST).isoformat())
    result = store_snapshot_and_transitions(
        chain, symbol, captured, str(payload.get('source') or 'NSE/Broker'),
        parse_num(payload.get('spot')), payload.get('expiry')
    )
    return {'ok': True, **result, 'db': 'SQLite WAL persistent market memory'}


@app.get('/memory/snapshots')
async def memory_snapshots(symbol: str = 'NIFTY', limit: int = 50):
    return {'symbol': symbol, 'snapshots': latest_snapshots(symbol, max(1, min(limit, 500)))}


@app.get('/memory/transition-stats')
async def memory_transition_stats(symbol: str = 'NIFTY'):
    return {
        'symbol': symbol,
        'stats': transition_stats(symbol),
        'meaning': 'A=baseline/no strong transition, B=developing transition, C=strong premium-up + OI-down transition; historical counts are descriptive, not predictive.'
    }


@app.post('/memory/maintenance')
async def memory_maintenance(vacuum: bool = False):
    result = prune_old_data(run_vacuum=vacuum)
    _metrics['maintenanceRuns'] += 1
    _metrics['lastMaintenance'] = result
    return result


@app.get('/memory/retention')
async def memory_retention():
    return retention_status()


@app.get('/feed/status')
async def feed_status():
    return {
        'provider': broker_manager.provider,
        'configured': bool(broker_manager.api_key and broker_manager.access_token),
        'mode': 'authenticated broker WebSocket when connected; otherwise NSE gateway/snapshot mode',
        'note': 'Do not place orders automatically from this prototype.',
        'broker': broker_manager.status(),
        'officialSource': official_source.status(),
        'tickWriter': tick_writer.stats(),
    }


@app.get('/ops/security')
async def security_status():
    return {
        'appAuthEnabled': bool(APP_ACCESS_TOKEN),
        'requireHttps': REQUIRE_HTTPS,
        'trustedOrigins': TRUSTED_ORIGINS,
        'osKeyringAvailable': secret_store.available(),
        'secretsPolicy': 'Session-memory by default; optional OS keyring only when explicitly requested.',
    }


@app.get('/ops/metrics')
async def ops_metrics():
    return {
        'timeIST': datetime.now(IST).isoformat(),
        'broker': broker_manager.status(),
        'officialSource': official_source.status(),
        'tickWriter': tick_writer.stats(),
        'memory': retention_status(),
        'metrics': _metrics,
    }


@app.get('/health')
def health():
    broker = broker_manager.status()
    db = retention_status()
    return {
        'ok': True,
        'service': 'nifty-option-ai-gateway',
        'timeIST': datetime.now(IST).isoformat(),
        'brokerConnected': broker['connected'],
        'lastTickAgeSec': broker['lastTickAgeSec'],
        'dbBytes': db['dbBytes'],
        'tickQueue': tick_writer.q.qsize(),
    }


@app.get('/clock')
def clock():
    now = datetime.now(IST)
    return {'nowIST': now.isoformat(), 'nowUTC': now.astimezone(UTC).isoformat()}


@app.get('/proxy/nse-option-chain')
async def nse_option_chain(symbol: str = 'NIFTY'):
    payload = await nse_get('/api/option-chain-indices', params={'symbol': symbol})
    out = normalize_option_chain(payload)
    try:
        refs = await build_reference_context('NIFTY 50', date.today()) if symbol.upper() == 'NIFTY' else {}
    except Exception as exc:
        refs = {}
        out['referenceStatus'] = f'Reference fetch failed: {exc}'
    out['references'] = refs
    out['referenceStatus'] = out.get('referenceStatus', 'NSE live option-chain + historical context checked')
    out['source'] = 'NSE'
    return out


@app.get('/proxy/nse-index-context')
async def nse_index_context(symbol: str = Query('NIFTY 50')):
    periods = await build_reference_context(symbol, date.today())
    return {
        'symbol': symbol, 'periods': periods, 'asofIST': datetime.now(IST).isoformat(),
        'note': 'Previous completed 1D/1W/1M reference periods from NSE Historical Index Data.', 'source': 'NSE'
    }


@app.get('/proxy/nse-index-history')
async def nse_index_history(
    symbol: str = Query('NIFTY 50'),
    from_date: str = Query(..., alias='from'),
    to_date: str = Query(..., alias='to'),
):
    start = datetime.strptime(from_date, '%d-%m-%Y').date()
    end = datetime.strptime(to_date, '%d-%m-%Y').date()
    return {'symbol': symbol, 'from': from_date, 'to': to_date, 'data': await fetch_index_history(symbol, start, end), 'source': 'NSE'}


@app.get('/broker/failover-status')
async def broker_failover_status():
    return failover.status()

@app.post('/broker/failover-configure')
async def broker_failover_configure(payload: dict[str, Any]):
    priority = payload.get('priority') or ['GROWW','DHAN']
    credentials = payload.get('credentials') or {}
    failover.configure(priority, credentials)
    return failover.status()

@app.post('/broker/failover-poll')
async def broker_failover_poll(symbol: str = 'NIFTY'):
    chain = failover.poll_once(symbol)
    if not chain:
        raise HTTPException(status_code=503, detail=failover.status().get('reason') or 'No validated source available')
    return {'chain': chain, 'failover': failover.status()}

@app.get('/broker/official-status')
async def official_status():
    return official_source.status()


@app.post('/broker/official-connect')
async def official_connect(payload: dict[str, Any]):
    provider=str(payload.get('provider','')).upper().strip()
    if provider == 'GROWW':
        token=str(payload.get('accessToken','')).strip()
        if not token: raise HTTPException(status_code=400, detail='Groww official API access token is required.')
        try:
            result=official_source.connect('GROWW', accessToken=token)
            failover.configure(['GROWW','DHAN'], {'GROWW': {'accessToken': token}})
            failover.switch_to('GROWW', 'manual connect')
            return result
        except Exception as exc: raise HTTPException(status_code=502, detail=f'Groww connection failed: {exc}')
    if provider == 'DHAN':
        cid=str(payload.get('clientId','')).strip(); token=str(payload.get('accessToken','')).strip()
        if not cid or not token: raise HTTPException(status_code=400, detail='Dhan client ID and access token are required.')
        ids=payload.get('underlyingIds') or {'NIFTY':13}
        try: ids={str(k):int(v) for k,v in ids.items()}
        except Exception: raise HTTPException(status_code=400, detail='Dhan underlying IDs must be numeric.')
        try:
            result=official_source.connect('DHAN', clientId=cid, accessToken=token, underlyingIds=ids)
            failover.configure(['DHAN','GROWW'], {'DHAN': {'clientId': cid, 'accessToken': token, 'underlyingIds': ids}})
            failover.switch_to('DHAN', 'manual connect')
            return result
        except Exception as exc: raise HTTPException(status_code=502, detail=f'Dhan connection failed: {exc}')
    raise HTTPException(status_code=400, detail='Choose GROWW or DHAN.')


@app.post('/broker/official-disconnect')
async def official_disconnect():
    official_source.disconnect(); return official_source.status()


@app.get('/broker/official-option-chain')
async def official_option_chain(symbol: str='NIFTY'):
    if not official_source.connected: raise HTTPException(status_code=409, detail='Official broker data source is not connected.')
    chain=official_source.get_chain(symbol)
    if not chain: raise HTTPException(status_code=503, detail=official_source.error or 'No option-chain data available.')
    return chain


@app.get('/broker/status')
async def broker_status():
    return broker_manager.status()


@app.post('/broker/connect')
async def broker_connect(payload: dict[str, Any]):
    provider = str(payload.get('provider', '')).upper().strip()
    remember = bool(payload.get('remember', False))
    if provider != 'ZERODHA':
        if provider == 'UPSTOX':
            raise HTTPException(status_code=501, detail='Upstox WebSocket adapter is not enabled in this build. Use Zerodha or an authorized vendor adapter.')
        raise HTTPException(status_code=400, detail='Unsupported broker. Choose ZERODHA.')
    api_key = str(payload.get('apiKey', '')).strip()
    access_token = str(payload.get('accessToken', '')).strip()
    if not api_key or not access_token:
        raise HTTPException(status_code=400, detail='API key and access token are required.')
    if remember:
        saved = secret_store.save(provider, api_key, access_token)
    else:
        saved = {'saved': False, 'reason': 'Session-only mode; credentials were not persisted.'}
    status = broker_manager.connect_zerodha(api_key, access_token)
    status['secretStorage'] = saved
    return status


@app.post('/broker/connect-saved')
async def broker_connect_saved(provider: str = Query('ZERODHA')):
    creds = secret_store.load(provider.upper())
    if not creds.get('apiKey') or not creds.get('accessToken'):
        raise HTTPException(status_code=404, detail='No saved broker credentials found in the OS keyring.')
    status = broker_manager.connect_zerodha(creds['apiKey'], creds['accessToken'])
    status['secretStorage'] = {'saved': True, 'source': 'OS keyring'}
    return status


@app.delete('/broker/saved')
async def broker_delete_saved(provider: str = Query('ZERODHA')):
    return secret_store.clear(provider.upper())


@app.post('/broker/disconnect')
async def broker_disconnect():
    broker_manager.disconnect()
    return broker_manager.status()


@app.websocket('/ws/market')
async def market_websocket(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        await websocket.send_json({'type':'hello','serverTime':datetime.now(IST).isoformat(),'provider':broker_manager.provider,'status':broker_manager.status()})
        while True:
            msg = await websocket.receive_json()
            if msg.get('type') == 'ping':
                await websocket.send_json({'type':'pong','serverTime':datetime.now(IST).isoformat()})
            elif msg.get('type') == 'subscribe':
                await websocket.send_json({'type':'subscribed','symbols':msg.get('symbols') or ['NIFTY']})
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)

@app.get('/market/candles')
async def market_candles(symbol: str='NIFTY', limit: int=Query(120, ge=1, le=2000)):
    rows = recent_candles(limit)
    # Persistent candle store may not contain broker candles yet; return current in-memory candle too.
    current = _chart_state.get(symbol.upper())
    if current:
        rows = rows + [current]
    return {'symbol':symbol.upper(),'interval':'1m','data':rows[-limit:]}


@app.get('/market/order-flow')
async def market_order_flow(symbol: str='NIFTY', limit: int=Query(120, ge=1, le=180)):
    rows = list(_order_flow_history.get(symbol.upper(), deque()))[-limit:]
    return {'symbol':symbol.upper(),'interval':'1m','mode':'inferred_tick_pressure','data':rows,
            'note':'Buy/sell pressure is inferred from live tick direction, volume change and bid/ask when available; it is not full exchange order-by-order depth.'}

@app.get('/market/stream-status')
async def market_stream_status():
    return {'websocketClients':len(_ws_clients),'provider':broker_manager.provider,'broker':broker_manager.status(),'officialSource':official_source.status(),'mode':'WebSocket ticks when broker supports it; official snapshot fallback otherwise'}

@app.get('/broker/ticks')
async def broker_ticks(limit: int = Query(500, ge=1, le=5000)):
    return {'data': recent_ticks(limit), 'count': limit}


@app.get('/broker/live-indices')
async def broker_live_indices():
    with _live_ticks_lock:
        rows = [dict(t) for t in _live_ticks.values() if str(t.get('instrument_type') or '').upper() in {'INDEX', ''}]
    return {'data': rows, 'count': len(rows), 'source': 'BROKER_WS'}
