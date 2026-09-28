"""AI layer trained on NSE option-chain features. Falls back to NSE rule-trend until a model exists."""
import os
import joblib, numpy as np
from nse_features import FEATURES, rule_p_up
MODEL_PATH = "data/model.joblib"
_model = None
_mtime = 0
def _load():
    global _model, _mtime
    if not os.path.exists(MODEL_PATH): return None
    m = os.path.getmtime(MODEL_PATH)
    if _model is None or m != _mtime:
        _model = joblib.load(MODEL_PATH); _mtime = m
    return _model
def p_up(f):
    m = _load()
    if m is None: return round(rule_p_up(f), 3), "NSE-rules"
    return round(float(m.predict_proba(np.array([[f[k] for k in FEATURES]]))[0][1]), 3), "NSE-ML"
def label(p):
    return "BULLISH" if p >= 0.58 else "BEARISH" if p <= 0.42 else "SIDEWAYS"
