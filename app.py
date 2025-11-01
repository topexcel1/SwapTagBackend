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
        total_fee = 2 #amount * (total_fee_percent / 100)
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


# configure the chat backend


# Initialize Gemini client
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def fallback_respond(message, context=None):
    """Simple fallback if Gemini API fails."""
    context = context or {}
    if "fee" in message.lower():
        return "Our current fee structure depends on transaction type and region. Please specify an amount or currency."
    elif "exchange" in message.lower():
        return "Exchange rates are updated frequently. You can check them using the fee simulator."
    elif "referral" in message.lower():
        return "You can earn bonuses by sharing your SwapTag referral link!"
    return "I’m sorry, I couldn’t process that. Could you please rephrase your question?"


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    POST payload:
    {
      "message": "Hello, how much fee for $100?",
      "swap_tag": "TEAMEX",
      "history": [
         {"role":"user","content":"..."},
         {"role":"assistant","content":"..."}
      ]
    }
    """
    payload = request.get_json() or {}
    user_msg = payload.get("message", "")
    swap_tag = payload.get("swap_tag")
    history = payload.get("history", [])

    if not user_msg:
        return jsonify({"error": "message_required"}), 400

    # Build system prompt
    system_prompt = (
        "You are VitalSwap Assistant. Respond clearly and concisely about fees, FX rates, "
        "the simulator, referral SwapTag usage, and general financial product questions. "
        "Avoid giving direct investment advice or exact rates unless provided by the system."
    )

    # Combine history + current message for context
    conversation = system_prompt + "\n\n"
    for h in history:
        role = h.get("role", "user")
        content = h.get("content", "")
        conversation += f"{role.capitalize()}: {content}\n"
    conversation += f"User: {user_msg}\nAssistant:"

    # Call Gemini API
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=conversation
        )
        assistant_text = response.text.strip()
    except Exception as e:
        assistant_text = fallback_respond(user_msg, context={"error": str(e), "swap_tag": swap_tag})

    # Save chat to database
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
            metaData=json.dumps({"via": "gemini", "sent_at": int(time.time())})
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
