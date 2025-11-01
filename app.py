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

# CORS - allow frontend origin(s)
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*')
# If ALLOWED_ORIGINS is a comma-separated list, split it
origins = [o.strip() for o in ALLOWED_ORIGINS.split(",")] if "," in ALLOWED_ORIGINS else ALLOWED_ORIGINS
CORS(app, origins=origins)

# Database initialization (uncomment when models exist)
# Base.metadata.create_all(bind=engine)



#CORS(app, origins=["https://swaptag-fee-page.netlify.app"])

# VitalSwap base API
BASE_URL = "https://2kbbumlxz3.execute-api.us-east-1.amazonaws.com/default"


# Fee structure
SERVICE_FEE_PERCENT = 1.5
PRODUCT_FEE_PERCENT = 0.5


# Cache for storing exchange rates temporarily
CACHE = {}  # {(from, to): {"rate": <float>, "timestamp": <epoch>}}
CACHE_TTL = 3600  # cache lifespan (1 hour)

# Fallback static exchange rates
FALLBACK_RATES = {
    ("USD", "NGN"): 1480.0,
    ("EUR", "NGN"): 1700.0,
    ("NGN", "USD"): 0.00068,
    ("NGN", "EUR"): 0.00059,
    ("USD", "EUR"): 0.93,
    ("EUR", "USD"): 1.08,
}


def get_cached_rate(from_currency, to_currency):
    """Return cached rate if valid (not expired), else None."""
    key = (from_currency, to_currency)
    cached = CACHE.get(key)
    if cached and (time.time() - cached["timestamp"] < CACHE_TTL):
        return cached["rate"]
    return None


def set_cached_rate(from_currency, to_currency, rate):
    """Store exchange rate in cache with current timestamp."""
    CACHE[(from_currency, to_currency)] = {"rate": rate, "timestamp": time.time()}


def get_live_rate(from_currency, to_currency):
    """Fetch live rate from exchangerate.host and cache it."""
    # Check cache first
    cached_rate = get_cached_rate(from_currency, to_currency)
    if cached_rate:
        return cached_rate

    # Fetch from external API
    try:
        url = f"https://api.exchangerate.host/convert?from={from_currency}&to={to_currency}"
        response = requests.get(url, timeout=5)
        data = response.json()

        rate = None
        if data.get("info") and "rate" in data["info"]:
            rate = float(data["info"]["rate"])
        elif "result" in data:
            rate = float(data["result"])

        if rate:
            set_cached_rate(from_currency, to_currency, rate)
        return rate
    except Exception:
        return None


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "SwapTag Exchange API (with caching) is running"}), 200


@app.route("/api/exchange", methods=["POST"])
def exchange():
    try:
        data = request.get_json()
        from_currency = data.get("from_currency")
        to_currency = data.get("to_currency")
        amount = float(data.get("amount", 0))

        # Validate input
        if not from_currency or not to_currency:
            return jsonify({"error": "Both 'from_currency' and 'to_currency' are required"}), 400
        if amount <= 0:
            return jsonify({"error": "Amount must be greater than zero"}), 400

        # Fetch live rate (with caching)
        fx_rate = get_live_rate(from_currency, to_currency)

        # Fallback if live rate unavailable
        if fx_rate is None:
            fx_rate = FALLBACK_RATES.get((from_currency, to_currency))

        if fx_rate is None:
            return jsonify({"error": "Exchange rate not available for this currency pair"}), 404

        # Calculate fees and conversion
        total_fee_percent = SERVICE_FEE_PERCENT + PRODUCT_FEE_PERCENT
        total_fee = amount * (total_fee_percent / 100)
        net_amount = amount - total_fee
        converted_amount = net_amount * fx_rate

        # Response object (frontend expects these fields)
        result = {
            "from_currency": from_currency,
            "to_currency": to_currency,
            "amount": amount,
            "fx_rate": round(fx_rate, 4),
            "service_fee": SERVICE_FEE_PERCENT,
            "product_fee": PRODUCT_FEE_PERCENT,
            "converted_amount": round(converted_amount, 2),
        }

        return jsonify(result), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)


# Optional OpenAI import; only used if API key is set
try:
    import openai
except Exception:
    openai = None

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
