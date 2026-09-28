import datetime as dt
import time
from fastapi import FastAPI, Header, HTTPException
import config as C
from angel_client import AngelClient
from nse_client import NSEClient
from nse_features import compute
from ai_model import p_up, label

app = FastAPI(title="NSE AI OI Paper Signal Server")
angel = AngelClient()
nse = NSEClient()
prev_angel = None
prev_nse = None
last_nse = None
last_nse_at = 0.0
position = None

@app.on_event("startup")
def startup():
    try:
        angel.login()
        angel.build_chain()
    except Exception as e:
        print("Angel startup warning:", e)

def market_open():
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    return now.weekday() < 5 and dt.time(9, 15) <= now.time() <= dt.time(15, 30)

def require_token(x_token):
    if C.API_TOKEN and x_token != C.API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid API token")

@app.get("/health")
def health(x_token: str = Header(default="")):
    require_token(x_token)
    return {"ok": True, "symbol": C.SYMBOL, "market_open": market_open()}

@app.get("/signal")
def signal(x_token: str = Header(default="")):
    global prev_angel, prev_nse, last_nse, last_nse_at, position
    require_token(x_token)
    if not market_open():
        return {"symbol": C.SYMBOL, "action": "WAIT", "market_open": False, "reasons": ["Market closed"]}

    try:
        snap = angel.snapshot()
    except Exception as e:
        return {"symbol": C.SYMBOL, "action": "WAIT", "market_open": True,
                "error": "Angel data unavailable: " + str(e)}

    nse_error = None
    if last_nse is None or time.time() - last_nse_at >= C.NSE_POLL_SEC:
        try:
            last_nse = nse.fetch(C.SYMBOL)
            last_nse_at = time.time()
        except Exception as e:
            nse_error = str(e)

    reasons = []
    out = {"symbol": C.SYMBOL, "action": "WAIT", "market_open": True,
           "spot": snap["spot"], "strike": snap["atm"], "nse_error": nse_error}

    if last_nse:
        f = compute(last_nse, prev_nse)
        prob, source = p_up(f)
        trend = label(prob)
        out["nse"] = {"trend": trend, "p_up": prob, "source": source,
                      "pcr": round(f["pcr_oi"], 3), "support": f["_support"],
                      "resistance": f["_resistance"], "max_pain": f["_maxpain"]}
        near_sr = f["dist_sup"] < 0.003 or f["dist_res"] < 0.003
        if near_sr:
            reasons.append("Spot is close to OI support/resistance")
    else:
        prob, source, trend = 0.5, "NSE-unavailable", "SIDEWAYS"
        near_sr = True
        reasons.append("NSE data unavailable")

    atm = snap["atm"]
    ce = snap["opts"].get((atm, "CE"))
    pe = snap["opts"].get((atm, "PE"))
    if not ce or not pe:
        return {**out, "reasons": reasons + ["ATM CE/PE quote unavailable"]}

    angel_score = 0.0
    candidate = None
    if prev_angel:
        for typ, item in (("CE", ce), ("PE", pe)):
            old = prev_angel["opts"].get((atm, typ))
            if not old or old["ltp"] <= 0:
                continue
            dp = item["ltp"] - old["ltp"]
            doi = item["oi"] - old["oi"]
            base = max(old["oi"], 1)
            magnitude = min(abs(doi) / base, 1.0)
            raw = (1 if dp > 0 else -1) * (
                0.6 * magnitude + 0.4 * min(abs(dp) / max(old["ltp"], 1), 1)
            )
            signed = raw if typ == "CE" else -raw
            if candidate is None or abs(signed) > abs(angel_score):
                angel_score = signed
                candidate = typ

    out["score"] = round(angel_score, 3)

    if candidate and abs(angel_score) >= C.THRESH and not near_sr:
        confidence = prob if candidate == "CE" else 1 - prob
        same = (candidate == "CE" and trend == "BULLISH") or (candidate == "PE" and trend == "BEARISH")
        if same and confidence >= C.AI_MIN_CONF:
            quote = ce if candidate == "CE" else pe
            entry = quote["ltp"]
            if entry > 0:
                sl = round(entry * (1 - C.SL_PCT), 2)
                target = round(entry * (1 + C.SL_PCT * C.RR), 2)
                position = {"type": candidate, "strike": atm, "entry": entry,
                            "sl": sl, "target": target}
                out.update({"action": "BUY_" + candidate, "type": candidate,
                            "entry": entry, "ltp": entry, "sl": sl, "target": target,
                            "ai_confidence": round(confidence, 3)})
                reasons.append("Angel OI/price and NSE AI direction agree")
        else:
            reasons.append("Angel and NSE AI directions do not agree")
    else:
        reasons.append("No qualifying paper entry")

    if position and out["action"] == "WAIT":
        quote = ce if position["type"] == "CE" else pe
        px = quote["ltp"]
        hit = px <= position["sl"] or px >= position["target"]
        reversal = ((position["type"] == "CE" and trend == "BEARISH") or
                    (position["type"] == "PE" and trend == "BULLISH"))
        if hit or reversal:
            out.update({"action": "EXIT", "type": position["type"],
                        "strike": position["strike"], "ltp": px,
                        "entry": position["entry"], "sl": position["sl"],
                        "target": position["target"]})
            reasons.append("SL/target or trend reversal")
            position = None

    prev_angel = snap
    prev_nse = last_nse
    out["reasons"] = reasons
    return out
