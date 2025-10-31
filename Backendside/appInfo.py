import os
import time
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'devkey')

# Allow only the frontend domain(s) by default
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*')
CORS(app, origins=[origin.strip() for origin in ALLOWED_ORIGINS.split(',')])

# ---------- Simple in-memory data (replace with DB for production) ----------
# Fee plans: flexible, used by frontend to present options and by calculator
FEES = [
    {"id": 1, "name": "Basic", "description": "For casual users", "fixed_fee": 0.50, "percent_fee": 0.005},
    {"id": 2, "name": "Standard", "description": "For active users", "fixed_fee": 0.25, "percent_fee": 0.0035},
    {"id": 3, "name": "Pro", "description": "For power users", "fixed_fee": 0.10, "percent_fee": 0.002}
]

# Example SwapTag (referral) metadata map (in prod this will be in DB)
SWAPTAG_META = {
    "TEAMEX": {"team": "Team Excellence", "payout_percent": 0.10},
    "TAIWO": {"team": "Taiwo", "payout_percent": 0.12}
}

# In-memory cache for FX rates (simple)
_fx_cache = {"rates": {}, "base": "USD", "ts": 0}
FX_TTL_SECONDS = 60 * 5  # cache FX for 5 minutes

FX_PROVIDER_URL = os.getenv('FX_PROVIDER_URL', 'https://api.exchangerate.host/latest')
FX_API_KEY = os.getenv('FX_API_KEY')  # optional, some providers require it

# ------------------- Utility: fetch FX rates (with caching) -------------------
def get_fx_rates(base='USD'):
    now = int(time.time())
    if _fx_cache["rates"] and _fx_cache["base"] == base and now - _fx_cache["ts"] < FX_TTL_SECONDS:
        return _fx_cache["rates"], _fx_cache["base"], _fx_cache["ts"]

    params = {"base": base}
    headers = {}
    if FX_API_KEY:
        # Example if provider requires key in header or params
        headers["Authorization"] = f"Bearer {FX_API_KEY}"
        # or params['access_key'] = FX_API_KEY depending on provider

    try:
        r = requests.get(FX_PROVIDER_URL, params=params, headers=headers, timeout=6)
        r.raise_for_status()
        data = r.json()
        # Many free providers return { rates: { 'EUR': 1.02, ... }, base: 'USD' }
        rates = data.get("rates", {})
        _fx_cache.update({"rates": rates, "base": data.get("base", base), "ts": now})
        return _fx_cache["rates"], _fx_cache["base"], _fx_cache["ts"]
    except Exception as e:
        # In production: log error and optionally return previous cached rates or raise 503
        if _fx_cache["rates"]:
            return _fx_cache["rates"], _fx_cache["base"], _fx_cache["ts"]
        raise

# ------------------------- Fee calculation logic -----------------------------
def calculate_fee(amount: float, fee_plan_id: int):
    """
    amount: in source currency units (float)
    fee_plan_id: id from FEES
    returns dict with fee breakdown
    """
    plan = next((p for p in FEES if p["id"] == int(fee_plan_id)), None)
    if not plan:
        raise ValueError("Invalid fee plan id")

    fixed = float(plan.get("fixed_fee", 0.0))
    percent = float(plan.get("percent_fee", 0.0))

    percent_amount = amount * percent
    total_fee = fixed + percent_amount
    net_amount = amount - total_fee

    return {
        "plan": plan["name"],
        "fixed_fee": round(fixed, 6),
        "percent_fee": percent,
        "percent_amount": round(percent_amount, 6),
        "total_fee": round(total_fee, 6),
        "net_amount": round(net_amount, 6)
    }

# ------------------------------- Routes -------------------------------------
@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "SwapTag Fee API"}), 200

@app.route("/api/fees", methods=["GET"])
def api_fees():
    """Return the fee plans for frontend to render"""
    return jsonify({"fees": FEES}), 200

@app.route("/api/fx/latest", methods=["GET"])
def api_fx_latest():
    """
    Query params:
      - base (optional) : e.g. USD
    """
    base = request.args.get("base", "USD").upper()
    try:
        rates, base_from, ts = get_fx_rates(base=base)
        return jsonify({"base": base_from, "ts": ts, "rates": rates}), 200
    except Exception as e:
        return jsonify({"error": "unable_to_fetch_fx", "message": str(e)}), 503

@app.route("/api/calculate", methods=["POST"])
def api_calculate():
    """
    Body:
    {
      "amount": 100.0,
      "currency": "USD",        # currency of amount
      "fee_plan_id": 1
    }
    """
    payload = request.get_json() or {}
    try:
        amount = float(payload.get("amount", 0))
        if amount <= 0:
            return jsonify({"error": "invalid_amount"}), 400
        fee_plan_id = int(payload.get("fee_plan_id", 1))
    except Exception:
        return jsonify({"error": "invalid_payload"}), 400

    try:
        result = calculate_fee(amount, fee_plan_id)
        result.update({"currency": payload.get("currency", "USD")})
        return jsonify({"calculation": result}), 200
    except ValueError as e:
        return jsonify({"error": "bad_request", "message": str(e)}), 400

@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    """
    Combined simulation:
    Body:
    {
      "amount": 100.0,
      "from_currency": "USD",
      "to_currency": "NGN",
      "fee_plan_id": 1,
      "swap_tag": "TAIWO"   # optional referral
    }
    """
    payload = request.get_json() or {}
    try:
        amount = float(payload.get("amount", 0))
        from_currency = payload.get("from_currency", "USD").upper()
        to_currency = payload.get("to_currency", "USD").upper()
        fee_plan_id = int(payload.get("fee_plan_id", 1))
        swap_tag = payload.get("swap_tag")
    except Exception:
        return jsonify({"error": "invalid_payload"}), 400

    # 1) Calculate fees in from_currency
    fee_breakdown = calculate_fee(amount, fee_plan_id)

    # 2) Get FX rate and convert net amount to destination currency
    try:
        rates, base, ts = get_fx_rates(base=from_currency)
        rate = rates.get(to_currency)
        if rate is None:
            return jsonify({"error": "unsupported_currency", "supported": list(rates.keys())}), 400

        net_amount = fee_breakdown["net_amount"]
        converted = round(net_amount * rate, 6)

        response = {
            "input": {"amount": amount, "from_currency": from_currency, "to_currency": to_currency},
            "fee": fee_breakdown,
            "fx": {"rate": rate, "base": base, "ts": ts},
            "converted_net_amount": converted
        }

        # 3) Attach SwapTag/referral info if provided
        if swap_tag:
            meta = SWAPTAG_META.get(swap_tag.upper(), {"team": "unknown", "payout_percent": 0})
            # Example referral link pattern your frontend can use:
            referral_link = f"https://swaptag-fee-page.netlify.app/?swaptag={swap_tag}"
            response["swap_tag"] = {"code": swap_tag, "meta": meta, "referral_link": referral_link}

        return jsonify(response), 200

    except Exception as e:
        return jsonify({"error": "fx_error", "message": str(e)}), 503

# Admin/debug route to view referrals (in-memory)
@app.route("/api/referrals", methods=["GET"])
def api_referrals():
    return jsonify({"swap_tags": SWAPTAG_META}), 200

# ------------------------------- Run ---------------------------------------
if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
