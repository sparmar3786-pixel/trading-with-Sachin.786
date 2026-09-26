# NIFTY Option AI — Production V2

## What changed
- Existing market formulas/decision calculations are treated as **read-only**.
- Added official Groww market-data connector using Groww Trading API.
- Added official DhanHQ v2 option-chain connector.
- Existing Zerodha WebSocket connector remains available.
- NSE live/CSV path remains available as verification/fallback.
- Added a provider-normalization layer so the calculation engine receives the same `chain/spot/expiry` structure regardless of provider.
- Added local 4-digit app MPIN lock in the UI with failed-attempt throttling. This is an app lock, not a substitute for broker authentication.
- Added provider health and option-chain freshness status.
- Added production documentation and connector tests.

## Important authentication rule
This build does **not** automate or scrape a broker's consumer app login/password/OTP/MPIN. It uses official broker developer authentication only. Groww currently documents Access Token, API Key+Secret and TOTP flows, and its official feed/option-chain APIs. Dhan documents Access Token/API-key based API access and an official option-chain API.

## Run
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r nifty_option_ai_live_v1/requirements.txt
uvicorn nifty_option_ai_live_v1.backend.server:app --host 0.0.0.0 --port 8000
```
Open `nifty_option_ai_live_v1/frontend/index.html` in a browser and set Gateway URL to the server URL.

## Groww
1. Enable/subscribe to Groww Trading API according to your Groww account.
2. Generate an official Access Token (or use the documented API-key/TOTP flow to obtain one).
3. Select **Groww — Official API** in the app.
4. Enter only the official access token in the secure connection form.

The connector uses Groww's official option-chain API and does not fabricate ΔOI when the provider does not supply it.

## Dhan
1. Obtain DhanHQ API credentials/token from Dhan.
2. Select **Dhan — Official API**.
3. Enter Client ID and Access Token.
4. NIFTY underlying security ID defaults to 13; keep this configurable because broker master data can change.

## Formula protection
Do not edit the existing setup/entry/SL/target/OI logic to add a provider. Provider-specific fields are normalized before entering the existing engine. This separation is deliberate.

## Live-data safety
- Provider failure => signal is not silently converted to valid fresh data.
- Stale/unverified data => current-trade gate remains blocked.
- Provider switch => the UI should wait for a fresh validated snapshot before a new trade decision.
- No automatic order placement is enabled in this release.

## Production deployment
Use HTTPS, a private backend, restricted CORS, an application access token, server-side secret storage, logging/monitoring, and a process supervisor/container. Do not expose broker credentials in frontend JavaScript or SQLite.
