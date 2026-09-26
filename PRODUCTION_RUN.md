# Production Run / Broker Setup

## Local

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn nifty_option_ai_live_v1.backend.server:app --host 127.0.0.1 --port 8000
```

Open `nifty_option_ai_live_v1/frontend/index.html`, set Gateway URL to `http://127.0.0.1:8000`, choose Zerodha, and paste the API key + current access token.

## Optional hardening

Copy `.env.example` to `.env` and set:

- `APP_ACCESS_TOKEN` — protects application endpoints with `X-App-Token`.
- `REQUIRE_HTTPS=1` — rejects non-HTTPS application requests.
- `TRUSTED_ORIGINS` — restricts CORS to the real frontend origin.
- retention values for ticks/snapshots/transitions.

The frontend has an **App access token** field for the optional application token. It is intentionally not persisted by the browser.

## Zerodha login/token rule

Use the broker's current official login flow to obtain the daily access token. Do not place the API secret in the browser application; the backend is the trust boundary. A fresh token must be supplied when the broker session expires.

## Monitoring

- `GET /health` — lightweight uptime check.
- `GET /feed/status` — broker/feed status.
- `GET /ops/metrics` — operational counters, queue depth, database size and broker health.
- `GET /memory/retention` — retention and database range report.

## Safe data semantics

The broker snapshot engine explicitly labels interval OI deltas as `broker_interval_snapshot_delta`. It does not claim that this is the exchange's official day-level change-in-OI value.
