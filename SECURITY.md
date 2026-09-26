# Security model

- Never put broker passwords, OTPs, TOTP secrets or access tokens in source code.
- Never send broker passwords to ChatGPT or commit them to Git.
- Use official broker API authentication.
- Keep secrets server-side and in OS keyring/secret manager where persistent storage is required.
- Use HTTPS in production.
- Restrict CORS to the deployed frontend origin.
- Keep automatic order placement disabled unless a separate, explicitly reviewed execution module is added.
- The 4-digit MPIN is a local app lock; it does not protect a compromised server or replace broker authentication.
