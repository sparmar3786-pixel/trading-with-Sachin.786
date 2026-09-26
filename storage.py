from __future__ import annotations
import json, sqlite3, os, threading, queue, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

DB_PATH = Path(os.getenv('AI_DB_PATH', Path(__file__).resolve().parent / 'market_memory.sqlite3'))
TICK_RETENTION_DAYS = int(os.getenv('TICK_RETENTION_DAYS', '7'))
SNAPSHOT_RETENTION_DAYS = int(os.getenv('SNAPSHOT_RETENTION_DAYS', '30'))
TRANSITION_RETENTION_DAYS = int(os.getenv('TRANSITION_RETENTION_DAYS', '365'))

SCHEMA = '''
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 captured_at TEXT NOT NULL,
 source TEXT NOT NULL,
 symbol TEXT,
 spot REAL,
 expiry TEXT,
 chain_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_time ON snapshots(symbol, captured_at);
CREATE TABLE IF NOT EXISTS ticks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 received_at TEXT NOT NULL,
 provider TEXT NOT NULL,
 instrument_token INTEGER,
 exchange TEXT,
 segment TEXT,
 tradingsymbol TEXT,
 name TEXT,
 strike REAL,
 expiry TEXT,
 instrument_type TEXT,
 last_price REAL,
 volume REAL,
 oi REAL,
 change REAL,
 payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time ON ticks(name, received_at);
CREATE INDEX IF NOT EXISTS idx_ticks_token_time ON ticks(instrument_token, received_at);
CREATE TABLE IF NOT EXISTS transitions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 snapshot_id INTEGER NOT NULL,
 previous_snapshot_id INTEGER,
 side TEXT NOT NULL,
 strike REAL NOT NULL,
 stage TEXT NOT NULL,
 score REAL NOT NULL,
 premium_change_pct REAL,
 oi_change_pct REAL,
 volume_change_pct REAL,
 reasons_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 FOREIGN KEY(snapshot_id) REFERENCES snapshots(id),
 FOREIGN KEY(previous_snapshot_id) REFERENCES snapshots(id)
);
CREATE INDEX IF NOT EXISTS idx_transitions_key ON transitions(side, strike, created_at);
CREATE TABLE IF NOT EXISTS signal_outcomes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 created_at TEXT NOT NULL,
 symbol TEXT NOT NULL,
 side TEXT NOT NULL,
 strike REAL NOT NULL,
 signal_stage TEXT NOT NULL,
 entry REAL,
 stop_loss REAL,
 target1 REAL,
 target2 REAL,
 exit_price REAL,
 outcome TEXT NOT NULL,
 bars_held INTEGER DEFAULT 0,
 max_favorable_pct REAL,
 max_adverse_pct REAL,
 context_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcomes_key ON signal_outcomes(symbol, side, strike, created_at);
CREATE TABLE IF NOT EXISTS regimes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 captured_at TEXT NOT NULL,
 symbol TEXT NOT NULL,
 regime TEXT NOT NULL,
 score REAL NOT NULL,
 metrics_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_regimes_symbol_time ON regimes(symbol, captured_at);
CREATE TABLE IF NOT EXISTS candles (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 captured_at TEXT NOT NULL,
 symbol TEXT NOT NULL,
 timeframe TEXT NOT NULL,
 open REAL, high REAL, low REAL, close REAL,
 volume REAL,
 source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candles_key ON candles(symbol,timeframe,captured_at);

CREATE TABLE IF NOT EXISTS maintenance_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ran_at TEXT NOT NULL,
 ticks_deleted INTEGER NOT NULL,
 snapshots_deleted INTEGER NOT NULL,
 transitions_deleted INTEGER NOT NULL,
 vacuumed INTEGER NOT NULL DEFAULT 0
);
'''

_init_lock = threading.Lock()


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _init_lock:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA synchronous=NORMAL')
        c.execute('PRAGMA busy_timeout=30000')
        c.executescript(SCHEMA)
        return c


def save_snapshot(captured_at, source, symbol, spot, expiry, chain):
    with connect() as db:
        cur = db.execute(
            'INSERT INTO snapshots(captured_at,source,symbol,spot,expiry,chain_json) VALUES(?,?,?,?,?,?)',
            (captured_at, source, symbol, spot, expiry, json.dumps(chain, separators=(',', ':')))
        )
        return cur.lastrowid


def latest_snapshots(symbol='NIFTY', limit=100):
    with connect() as db:
        rows = db.execute('SELECT * FROM snapshots WHERE symbol=? ORDER BY id DESC LIMIT ?', (symbol, limit)).fetchall()
    return [dict(r) for r in rows]


def save_transition(snapshot_id, prev_id, side, strike, stage, score, p, oi, vol, reasons):
    return save_transitions_batch([{
        'snapshot_id': snapshot_id, 'previous_snapshot_id': prev_id, 'side': side, 'strike': strike,
        'stage': stage, 'score': score, 'premium_change_pct': p, 'oi_change_pct': oi,
        'volume_change_pct': vol, 'reasons': reasons
    }])


def save_transitions_batch(items: list[dict[str, Any]]) -> int:
    if not items:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    rows = [(
        x['snapshot_id'], x.get('previous_snapshot_id'), x['side'], x['strike'], x['stage'], x['score'],
        x.get('premium_change_pct'), x.get('oi_change_pct'), x.get('volume_change_pct'),
        json.dumps(x.get('reasons') or []), now
    ) for x in items]
    with connect() as db:
        db.executemany(
            'INSERT INTO transitions(snapshot_id,previous_snapshot_id,side,strike,stage,score,premium_change_pct,oi_change_pct,volume_change_pct,reasons_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            rows
        )
    return len(rows)


def transition_stats(symbol='NIFTY', limit=1000):
    with connect() as db:
        rows = db.execute(
            '''SELECT side,stage,COUNT(*) n,AVG(score) avg_score,AVG(premium_change_pct) avg_p,AVG(oi_change_pct) avg_oi,AVG(volume_change_pct) avg_vol
               FROM transitions t JOIN snapshots s ON s.id=t.snapshot_id
               WHERE s.symbol=? GROUP BY side,stage ORDER BY side,stage''',
            (symbol,)
        ).fetchall()
    return [dict(r) for r in rows]


def save_ticks_batch(ticks: list[dict[str, Any]]) -> int:
    if not ticks:
        return 0
    rows = []
    for tick in ticks:
        rows.append((
            tick.get('received_at_iso'), tick.get('provider'), tick.get('instrument_token'),
            tick.get('exchange'), tick.get('segment'), tick.get('tradingsymbol'), tick.get('name'),
            tick.get('strike'), tick.get('expiry'), tick.get('instrument_type'), tick.get('last_price'),
            tick.get('volume'), tick.get('oi'), tick.get('change'),
            json.dumps(tick, separators=(',', ':'), default=str)
        ))
    with connect() as db:
        db.executemany(
            'INSERT INTO ticks(received_at,provider,instrument_token,exchange,segment,tradingsymbol,name,strike,expiry,instrument_type,last_price,volume,oi,change,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            rows
        )
    return len(rows)


def save_tick(tick):
    return save_ticks_batch([tick])


def recent_ticks(limit=500):
    with connect() as db:
        rows = db.execute('SELECT * FROM ticks ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(r) for r in rows]


def retention_status() -> dict[str, Any]:
    with connect() as db:
        ticks = db.execute('SELECT COUNT(*) c, MIN(received_at) min_ts, MAX(received_at) max_ts FROM ticks').fetchone()
        snapshots = db.execute('SELECT COUNT(*) c, MIN(captured_at) min_ts, MAX(captured_at) max_ts FROM snapshots').fetchone()
        transitions = db.execute('SELECT COUNT(*) c, MIN(created_at) min_ts, MAX(created_at) max_ts FROM transitions').fetchone()
        size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return {
        'dbPath': str(DB_PATH),
        'dbBytes': size,
        'ticks': dict(ticks),
        'snapshots': dict(snapshots),
        'transitions': dict(transitions),
        'retentionDays': {
            'ticks': TICK_RETENTION_DAYS,
            'snapshots': SNAPSHOT_RETENTION_DAYS,
            'transitions': TRANSITION_RETENTION_DAYS,
        }
    }


def prune_old_data(run_vacuum: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    tick_cutoff = (now - timedelta(days=TICK_RETENTION_DAYS)).isoformat()
    snapshot_cutoff = (now - timedelta(days=SNAPSHOT_RETENTION_DAYS)).isoformat()
    transition_cutoff = (now - timedelta(days=TRANSITION_RETENTION_DAYS)).isoformat()
    with connect() as db:
        cur1 = db.execute('DELETE FROM ticks WHERE received_at < ?', (tick_cutoff,))
        cur2 = db.execute('DELETE FROM snapshots WHERE captured_at < ?', (snapshot_cutoff,))
        cur3 = db.execute('DELETE FROM transitions WHERE created_at < ?', (transition_cutoff,))
        db.execute('INSERT INTO maintenance_log(ran_at,ticks_deleted,snapshots_deleted,transitions_deleted,vacuumed) VALUES(?,?,?,?,?)',
                   (now.isoformat(), cur1.rowcount, cur2.rowcount, cur3.rowcount, 1 if run_vacuum else 0))
    if run_vacuum:
        with connect() as db:
            db.execute('VACUUM')
    return {
        'ticksDeleted': max(cur1.rowcount, 0),
        'snapshotsDeleted': max(cur2.rowcount, 0),
        'transitionsDeleted': max(cur3.rowcount, 0),
        'vacuumed': run_vacuum,
        'ranAt': now.isoformat(),
    }


class TickWriter:
    """Buffered DB writer: avoids opening a SQLite transaction for every market tick."""
    def __init__(self, batch_size: int = 100, flush_interval: float = 0.75):
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=int(os.getenv('TICK_QUEUE_MAX', '20000')))
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name='tick-writer', daemon=True)
        self.started = False
        self.enqueued = 0
        self.dropped = 0
        self.written = 0
        self.last_flush = None

    def start(self):
        if not self.started:
            self.started = True
            self.thread.start()

    def submit(self, tick: dict[str, Any]):
        try:
            self.q.put_nowait(tick)
            self.enqueued += 1
        except queue.Full:
            self.dropped += 1

    def _run(self):
        while not self.stop_event.is_set():
            batch = []
            try:
                batch.append(self.q.get(timeout=self.flush_interval))
            except queue.Empty:
                pass
            started = time.monotonic()
            while len(batch) < self.batch_size and (time.monotonic() - started) < self.flush_interval:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            if batch:
                try:
                    self.written += save_ticks_batch(batch)
                    self.last_flush = time.time()
                except Exception:
                    # Preserve the queue contract; callers still see watchdog/health state.
                    self.dropped += len(batch)

    def stats(self):
        return {
            'queueSize': self.q.qsize(),
            'enqueued': self.enqueued,
            'written': self.written,
            'dropped': self.dropped,
            'lastFlush': self.last_flush,
            'running': self.started and not self.stop_event.is_set(),
        }

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)


tick_writer = TickWriter()


def save_signal_outcome(item: dict[str, Any]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        cur = db.execute(
            "INSERT INTO signal_outcomes(created_at,symbol,side,strike,signal_stage,entry,stop_loss,target1,target2,exit_price,outcome,bars_held,max_favorable_pct,max_adverse_pct,context_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now,item['symbol'],item['side'],item['strike'],item.get('signal_stage','C'),item.get('entry'),item.get('stop_loss'),item.get('target1'),item.get('target2'),item.get('exit_price'),item['outcome'],item.get('bars_held',0),item.get('max_favorable_pct'),item.get('max_adverse_pct'),json.dumps(item.get('context') or {},separators=(',',':')))
        )
        return cur.lastrowid

def outcome_stats(symbol='NIFTY'):
    with connect() as db:
        rows=db.execute("SELECT outcome,COUNT(*) n,AVG(max_favorable_pct) avg_fav,AVG(max_adverse_pct) avg_adv,AVG(bars_held) avg_bars FROM signal_outcomes WHERE symbol=? GROUP BY outcome ORDER BY outcome",(symbol,)).fetchall()
        total=db.execute("SELECT COUNT(*) c FROM signal_outcomes WHERE symbol=?",(symbol,)).fetchone()['c']
    return {'total':total,'byOutcome':[dict(r) for r in rows]}

def save_regime(symbol, captured_at, regime, score, metrics):
    with connect() as db:
        db.execute('INSERT INTO regimes(captured_at,symbol,regime,score,metrics_json) VALUES(?,?,?,?,?)',(captured_at,symbol,regime,score,json.dumps(metrics,separators=(',',':'))))

def recent_regimes(symbol='NIFTY',limit=20):
    with connect() as db:
        rows=db.execute('SELECT * FROM regimes WHERE symbol=? ORDER BY id DESC LIMIT ?',(symbol,limit)).fetchall()
    return [dict(r) for r in rows]

def save_candle(item):
    with connect() as db:
        db.execute('INSERT INTO candles(captured_at,symbol,timeframe,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?,?)',(item['captured_at'],item['symbol'],item['timeframe'],item.get('open'),item.get('high'),item.get('low'),item.get('close'),item.get('volume'),item.get('source','BROKER_WS')))

def recent_candles(symbol='NIFTY',timeframe='1m',limit=100):
    with connect() as db:
        rows=db.execute('SELECT * FROM candles WHERE symbol=? AND timeframe=? ORDER BY id DESC LIMIT ?',(symbol,timeframe,limit)).fetchall()
    return [dict(r) for r in rows]
