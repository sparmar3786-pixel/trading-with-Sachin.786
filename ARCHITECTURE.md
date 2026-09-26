# Architecture

```text
Groww / Dhan / Zerodha / NSE CSV
              |
              v
      Provider adapters
              |
              v
       Normalized market state
              |
              +--> persistent tick/snapshot memory
              |
              v
      EXISTING CALCULATION ENGINE
              |
      Entry / SL / Target / Exit
              |
              v
          UI + AI Audit
```

### Provider boundary
`backend/adapters/*` and `backend/data_source.py` are the only places where provider-specific fields are interpreted. This prevents broker-specific changes from leaking into the trading formulas.

### Data freshness
A source timestamp must come from the source/validated server response. Browser upload time is never treated as market capture time.

### Failover
The UI can switch provider. The system must not carry forward an old provider's chain as if it were a new snapshot. A new provider must deliver a fresh snapshot before current-trade status can become valid.
