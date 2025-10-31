import os
import time
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
import json
from sqlalchemy.orm import Session
from database import SessionLocal, engine, Base
from models import Referral, ChatMessage


load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'devkey')

# Allow only the frontend domain(s) by default
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*')
CORS(app, origins=[origin.strip() for origin in ALLOWED_ORIGINS.split(',')])

# Ensure DB tables exist
Base.metadata.create_all(bind=engine)

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

# Optional OpenAI import; only used if API key is set
try:
    import openai
except Exception:
    openai = None

#load_dotenv()

#app = Flask(__name__)
#CORS(app, origins=[os.getenv("ALLOWED_ORIGINS", "*")])



# OpenAI settings (optional)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # change as needed
if OPENAI_API_KEY and openai:
    openai.api_key = OPENAI_API_KEY

# Simple safe fallback responder if no OpenAI key is available
def fallback_respond(user_message: str, context: dict = None) -> str:
    # Basic rule-based responses for the fee page context
    msg = user_message.lower().strip()
    if "fee" in msg or "fees" in msg:
        return ("You can view current fees by clicking the fee list or using the simulator. "
                "Try the 'Simulate' button to estimate fees for a specific swap.")
    if "simulate" in msg or "calculator" in msg:
        return ("Use the calculator to enter an amount, choose currencies and a plan, "
                "then press Simulate to see fees and the converted amount.")
    if "referral" in msg or "swaptag" in msg:
        return ("Include your SwapTag in the simulator's referral field and we'll track conversions for your team.")
    if "rate" in msg or "fx" in msg or "exchange" in msg:
        return ("FX rates are available in the 'FX' dropdown. For USD→NGN, we use VitalSwap's official rates.")
    # default
    return ("Hi — I can answer questions about fees, the simulator, referrals, and FX rates. "
            "Ask me something like: 'How much fee for $100 USD to NGN?'")

def call_openai_chat(messages):
    """
    messages: list of dicts e.g. [{"role": "system", "content": "..."},
                                   {"role": "user",   "content": "..."}]
    returns assistant text
    """
    if not openai:
        raise RuntimeError("OpenAI client not installed")
    # adjust for model API differences; this uses the Chat Completions API pattern
    resp = openai.ChatCompletion.create(
        model=OPENAI_MODEL,
        messages=messages,
        max_tokens=400,
        temperature=0.2
    )
    # The typical path for responses:
    text = resp.choices[0].message.get("content", "").strip()
    return text

# -------------------------
# Chat endpoints
# -------------------------

@app.route("/api/chat", methods=["POST"])
def chat():
    """
    POST payload:
    {
      "message": "Hello, how much fee for $100?",
      "swap_tag": "TEAMEX",         # optional: link conversation to referral
      "history": [                  # optional: past messages to provide context (list of {role,content})
         {"role":"user","content":"..."},
         {"role":"assistant","content":"..."}
      ]
    }
    """
    payload = request.get_json() or {}
    user_msg = payload.get("query", "")
    swap_tag = payload.get("swap_tag")
    history = payload.get("history", [])  # optional

    if not user_msg:
        return jsonify({"error": "message_required"}), 400

    # Build conversation for model: include optional system prompt to ground behavior
    system_prompt = {
        "role": "system",
        "content": (
            "You are VitalSwap Assistant. Answer clearly and concisely about fees, FX rates, "
            "the simulator, referral SwapTag usage, and general product questions. "
            "If the user asks for code or direct financial advice, be clear about assumptions."
        )
    }

    # Compose messages: system + history + user
    messages = [system_prompt]
    # Accept history if given (validate roles)
    for item in history:
        if item.get("role") in ("user", "assistant", "system") and item.get("content"):
            messages.append({"role": item["role"], "content": item["content"]})

    messages.append({"role": "user", "content": user_msg})

    # Try OpenAI if API key present, otherwise fallback
    try:
        if OPENAI_API_KEY and openai:
            assistant_text = call_openai_chat(messages)
        else:
            assistant_text = fallback_respond(user_msg, context={"swap_tag": swap_tag})
    except Exception as e:
        # On model failure, use fallback
        assistant_text = fallback_respond(user_msg, context={"error": str(e), "swap_tag": swap_tag})

    # Persist both user message and assistant reply to DB
    db: Session = SessionLocal()
    try:
        user_record = ChatMessage(
            swap_tag=swap_tag,
            role="user",
            content=user_msg,
            metaData=json.dumps({"source": "frontend", "received_at": int(time.time())})
        )
        db.add(user_record)
        db.commit()
        db.refresh(user_record)

        assistant_record = ChatMessage(
            swap_tag=swap_tag,
            role="assistant",
            content=assistant_text,
            metaData=json.dumps({"via": "openai" if OPENAI_API_KEY and openai else "fallback", "sent_at": int(time.time())})
        )
        db.add(assistant_record)
        db.commit()
        db.refresh(assistant_record)
    finally:
        db.close()

    return jsonify({
        "reply": assistant_text,
        "swap_tag": swap_tag,
        "saved": True
    }), 200


@app.route("/api/chat/history", methods=["GET"])
def chat_history():
    """
    Query params:
      - swap_tag (optional)  -> filter by swap_tag
      - limit (optional)
    """
    swap_tag = request.args.get("swap_tag")
    limit = int(request.args.get("limit", 200))

    db: Session = SessionLocal()
    try:
        query = db.query(ChatMessage)
        if swap_tag:
            query = query.filter(ChatMessage.swap_tag == swap_tag)
        query = query.order_by(ChatMessage.timestamp.asc()).limit(limit)
        messages = query.all()

        out = [
            {
                "id": m.id,
                "swap_tag": m.swap_tag,
                "role": m.role,
                "content": m.content,
                "metaData": (m.metadata and json.loads(m.metadata)) or None,
                "timestamp": m.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            }
            for m in messages
        ]
    finally:
        db.close()

    return jsonify({"count": len(out), "messages": out}), 200

# ------------------------------- Run ---------------------------------------
if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
