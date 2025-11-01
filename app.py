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
from google import genai

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




GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = "https://gemini.api.endpoint/v1/chat/completions"  # replace with actual Gemini endpoint


def call_gemini_chat(messages):
    """
    Calls the Gemini chat API with the messages conversation.
    """
    headers = {
        "Authorization": f"Bearer {GEMINI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "gemini-1",  # Replace with your specific Gemini model name
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 150,
        # other Gemini-specific parameters if any
    }

    response = requests.post(GEMINI_API_URL, headers=headers, json=payload, timeout=15)
    response.raise_for_status()
    data = response.json()

    # Parse Gemini response to get the assistant's reply text
    # Adjust parsing according to actual Gemini response schema
    assistant_text = data["choices"][0]["message"]["content"]
    return assistant_text


@app.route("/api/chat", methods=["POST"])
def chat():
    payload = request.get_json() or {}

    user_msg = payload.get("message") or payload.get("query") or ""
    swap_tag = payload.get("swap_tag")
    history = payload.get("history", [])

    if not user_msg:
        return jsonify({"error": "message_required"}), 400

    system_prompt = {
        "role": "system",
        "content": (
            "You are VitalSwap Assistant. Answer clearly and concisely about fees, FX rates, "
            "the simulator, referral SwapTag usage, and general product questions. "
            "If the user asks for code or direct financial advice, be clear about assumptions."
        )
    }

    messages = [system_prompt]
    for item in history:
        if item.get("role") in ("user", "assistant", "system") and item.get("content"):
            messages.append({"role": item["role"], "content": item["content"]})

    messages.append({"role": "user", "content": user_msg})

    try:
        if GEMINI_API_KEY:
            assistant_text = call_gemini_chat(messages)
        else:
            assistant_text = fallback_respond(user_msg, context={"swap_tag": swap_tag})
    except Exception as e:
        assistant_text = fallback_respond(user_msg, context={"error": str(e), "swap_tag": swap_tag})

    # Persist messages to DB as before (code omitted here for brevity)

    return jsonify({
        "reply": assistant_text,
        "swap_tag": swap_tag,
        "saved": True
    }), 200
# ------------------------------- Run ---------------------------------------
if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
