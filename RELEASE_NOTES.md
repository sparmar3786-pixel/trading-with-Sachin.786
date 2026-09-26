# V2.0 Production Upgrade

## Preserved
The existing option-trading calculation/decision engine was not rewritten. Provider integration is outside the formula layer.

## Added
- Groww official API option-chain connector.
- DhanHQ official API option-chain connector.
- Existing Zerodha WebSocket connector retained.
- Unified provider-normalization layer.
- Freshness/transition-safe provider switching.
- Local 4-digit app MPIN lock with throttled failures.
- Android WebView shell + GitHub Actions APK build.
- Security/architecture/deployment documentation.
- Connector unit tests.

## Deliberately not added
- Consumer broker password scraping.
- OTP/MPIN interception.
- Automatic order placement.
- Artificial file-size padding.
- Changes to OI/entry/SL/target formulas.
