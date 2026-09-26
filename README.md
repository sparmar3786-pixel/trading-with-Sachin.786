# NIFTY Option AI — Seller Map & Signal Engine V2 (COPY)

This COPY contains the expanded rule engine discussed for the option-chain analyzer. The original V1 ZIP remains separate and is not modified by this build.

## Added logic

1. **Freshness gate**
   - Fresh trade window: 300 seconds (5 minutes).
   - CSV upload time is never treated as market-data time.
   - Live NSE feed uses the option-chain timestamp (`Updated AT` / normalized records timestamp).
   - Manual CSV capture time is available for testing. Without a verified capture time, current-trade output is BLOCKED.

2. **Option-chain parsing**
   - NSE 23-column CSV parser.
   - CE/PE OI, change in OI, volume, IV, LTP, change, bid/ask.
   - Live NSE gateway returns a normalized chain plus spot, expiry and timestamp.

3. **Short-covering logic**
   - Core evidence: premium up + OI down + volume expansion.
   - Static snapshot: premium up + session OI down + high activity = covering evidence/watch.
   - Snapshot transition: premium up + OI down + volume expansion = Stage C acceleration.
   - Exact hidden seller SL is explicitly not claimed to be observable.

4. **A → B → C transition monitor**
   - Stage A: baseline / weak evidence.
   - Stage B: premium or OI transition starts.
   - Stage C: premium up + OI down + volume expansion in the latest transition.
   - Repeated snapshots are retained in browser state for transition comparison.

5. **Opposite-side short-covering override**
   - PE BUY is blocked/re-evaluated when strong nearby CE short covering is active and spot is in/near the trigger zone.
   - CE BUY is blocked/re-evaluated when strong nearby PE short covering is active and spot is in/near the trigger zone.
   - This directly addresses the failure mode where PE signals continued despite broad CE short covering.

6. **No-bars / confidence guard**
   - The engine distinguishes option-chain snapshots from true intraday bars.
   - High-confidence status requires at least 3 distinct intraday minutes with spot snapshots. Otherwise the signal remains WATCH/limited even when structure is strong.

7. **Buyer setup**
   - CE/PE directional scores from short-covering, possible opposite-side writing/build-up, spot vs key OI zones and override rules.
   - Includes reference entry, premium SL and R-multiple targets. These are configurable risk-model references, not guarantees.

8. **Seller map**
   - High-OI call resistance / put support.
   - Probable short-covering strike zones.
   - Possible writing/build-up zones.
   - Exact seller stop-loss remains hidden and is not fabricated.

9. **Combined CE + PE buy watch**
   - Flags synchronized near-ATM expansion when both legs rise in premium while OI falls between snapshots.
   - This is a volatility-expansion watch, not a promise that a straddle/strangle will profit.

10. **Previous completed 1D / 1W / 1M references**
   - Gateway requests NSE Historical Index Data.
   - 1D = latest completed trading day before the current as-of date.
   - 1W = previous completed calendar week.
   - 1M = previous completed calendar month.
   - High, low, close and reference dates are shown with the source.

11. **Payoff calculator**
   - Long/short CE and PE.
   - Expiry intrinsic value and P/L.
   - Current approximate intrinsic P/L.
   - Break-even formulas.
   - Margin is shown conceptually as collateral/risk requirement, not maximum loss.

12. **Live refresh**
   - Manual refresh plus 30-second auto-refresh.
   - The gateway seeds an NSE web session and returns normalized market data.
   - Production use should prefer an authorized broker/market-data API with server-side credentials, caching, throttling, failover and monitoring.

## Run frontend

Open `frontend/index.html` in a modern browser for CSV mode.

## Run live gateway

```bash
pip install fastapi uvicorn httpx
uvicorn backend.server:app --host 0.0.0.0 --port 8000
```

Then set the Gateway URL to `http://localhost:8000` and use **Refresh Live** or **Live: ON**.

## Important data limitations

- Public OI does not identify the exact buyer/seller on the opposite side of each trade.
- A rise in premium + fall in OI is evidence consistent with short covering, not proof of one particular participant's stop-loss.
- CSV upload time is not market-data capture time.
- The app will block a current-trade signal when freshness cannot be verified inside the configured 5-minute window.
- Lot size should be checked against the current NSE contract specification before calculating rupee P/L.

## Official reference

NSE's official option-chain page exposes OI, change in OI, volume, IV, LTP, bid/ask and an `Updated AT` timestamp, with CSV download support. NSE also exposes historical index data with 1D/1W/1M range controls.

## AI Market Auditor + Multi-Index Watch (added in COPY)
- Chat-style **AI Market Auditor** panel performs an auditable rule-based review of the live option-chain state.
- It checks freshness, intraday-bar availability, PCR(OI), CE/PE short-covering signatures, and blocks unsupported high-confidence conclusions.
- **Multi-Index Live Watch** polls NSE's all-indices endpoint and displays a configurable watch set including NIFTY 50, NIFTY BANK, NIFTY FINANCIAL SERVICES, NIFTY MIDCAP 50, NIFTY NEXT 50, NIFTY IT, NIFTY PHARMA, NIFTY AUTO, NIFTY INDIA VIX and other available indices.
- Browser notifications can be enabled by the user. The app alerts on material index moves and signal-state changes.
- This is inspired by the workflow pattern of products such as Zing: live structured signals, TP/SL context, and one intelligent view. It is **not a copy of Zing's proprietary strategy/IP**.
- The AI layer reports observable evidence and does not claim to know hidden buyer/seller identities or private stop-loss orders.
- NSE live/real-time data access is subject to NSE/NSE Data licensing and applicable usage terms. For production-grade low-latency feeds, use an authorized data vendor/broker or licensed NSE feed rather than relying on website scraping.

## Broker live-feed connection (COPY build)

The app now includes a Broker Live Feed panel. Select **Zerodha Kite Connect**, enter the API key and current access token, and press **Connect**. The server starts an authenticated WebSocket (`KiteTicker`), auto-discovers active NIFTY/BANKNIFTY instruments and subscribes in full mode. Incoming ticks are persisted into `backend/market_memory.sqlite3` for replay/audit and A→B→C transition memory.

Credentials and access tokens are intentionally **not written to SQLite or browser localStorage**. The access token is broker-controlled and may need to be refreshed according to the broker's login/session rules.

### Zerodha setup
1. Create/enable a Kite Connect app in the Zerodha developer console.
2. Supply the API key and the current access token in the app's Broker Live Feed panel.
3. Connect during market hours and confirm `CONNECTED` plus a recent `last tick`.
4. Do not commit or share API keys/access tokens.

The app uses WebSocket streaming for live data rather than repeatedly polling quote endpoints. Kite Connect's public documentation/forum guidance describes WebSocket as the live streaming mechanism and recommends building live candles from streamed ticks; historical APIs are intended for historical/backtesting use. See the official documentation/forum links in the project notes.

### Production note
For a production deployment, use a secure server-side secret store, HTTPS, authenticated user sessions, reconnect/backoff, health checks, retention/partitioning for tick data, and the broker's current official SDK/version. The included adapter is a practical local/controlled-environment implementation, not a claim of guaranteed uptime or execution quality.


## Production hardening added in COPY

The COPY build now includes an operational hardening layer rather than only a demo connector:

- **Broker reconnect/backoff:** Zerodha `KiteTicker` is configured with broker-supported exponential reconnect settings and an application watchdog. The current Python client documents `reconnect_max_tries`, `reconnect_max_delay`, `connect_timeout`, `on_reconnect`, and `on_noreconnect`; the app never fabricates a new access token. If authentication expires, the user must supply a fresh token and reconnect. 
- **Stall watchdog:** if a connected broker feed stops delivering ticks beyond the configured threshold, the server rebuilds the WebSocket session. A stale feed is surfaced in `/health` and `/ops/metrics`.
- **Buffered tick writer:** ticks are queued and written to SQLite in batches, using WAL mode and a busy timeout instead of opening a separate transaction for every tick.
- **Retention/maintenance:** configurable retention automatically removes old tick/snapshot/transition records. `/memory/retention` reports database size and ranges; `/memory/maintenance` performs manual cleanup.
- **Broker-driven snapshots:** every configurable interval (default 30s), the server builds NIFTY/BANKNIFTY option snapshots from the latest WebSocket ticks and stores A→B→C transition evidence. The broker interval OI delta is explicitly labeled; it is not represented as NSE's official day-change-in-OI field.
- **Health & observability:** `/health`, `/feed/status`, `/ops/metrics`, and `/ops/security` expose feed age, reconnect counts, queue state, database size, and security configuration.
- **Optional application authentication:** set `APP_ACCESS_TOKEN`; the frontend has an optional App access token field and sends it in `X-App-Token`. This is off by default for local use.
- **HTTPS enforcement:** set `REQUIRE_HTTPS=1` behind a TLS reverse proxy. The server honors `X-Forwarded-Proto` and rejects non-HTTPS application requests when enabled.
- **CORS control:** set `TRUSTED_ORIGINS` to the exact browser origins for deployment.
- **Secret handling:** credentials stay in server memory by default. If the user explicitly checks “Remember in OS keyring”, the optional `keyring` package stores the broker credentials in the server's operating-system credential backend; they are not written to SQLite or browser localStorage. A daily access token is still subject to broker session rules.
- **Rate-limit discipline:** live data uses WebSocket streaming rather than repeated quote polling. The app uses the broker instrument dump only when establishing the session and does not poll quote endpoints in the tick loop.

### Important Zerodha limits

Zerodha's current Python KiteTicker documentation states that WebSocket auto-reconnect is supported with configurable maximum retries/delay, and the documented client limits include up to 3 concurrent WebSocket connections per API key and up to 3000 instruments per connection. The COPY build caps its own socket groups at 2800 instruments to keep headroom. Do not increase these limits without checking the broker's current documentation/terms.

### Broker OI semantics

A raw broker tick gives current OI, but the connector does not treat that as NSE's official daily `CHNG IN OI`. For broker-built 30-second snapshots, `doi` means **change in OI between the app's previous and current broker snapshots**. This is useful for A→B→C transition detection but must not be presented as the official exchange day-change field.

### Production deployment shape

Recommended deployment:

`Browser (HTTPS) → reverse proxy/TLS → FastAPI gateway → broker WebSocket session + tick buffer → SQLite/PostgreSQL market memory → A/B/C transition engine → AI auditor/notifications`

For a serious multi-user deployment, replace local SQLite with PostgreSQL/TimescaleDB, move secrets to a managed secrets service, add per-user authentication/authorization, and run the broker collector as a supervised service rather than in the same process as the browser API.
