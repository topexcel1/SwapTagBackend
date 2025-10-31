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
#CORS(app, origins=[origin.strip() for origin in ALLOWED_ORIGINS.split(',')])


app = Flask(__name__)
CORS(app, origins=[os.getenv("ALLOWED_ORIGINS", "*")])

# Initialize DB
Base.metadata.create_all(bind=engine)

# Environment Variables
BASE_URL = os.getenv("BASE_URL")
REFERRAL_BONUS_RATE = float(os.getenv("REFERRAL_BONUS_RATE", 0.10))
FX_CACHE = {"data": None, "timestamp": 0}


@app.route("/")
def home():
    return jsonify({
        "message": "VitalSwap Fee Backend with PostgreSQL Referral Tracking",
        "endpoints": ["/api/fees", "/api/exchange", "/api/simulate", "/api/referrals"]
    })


@app.route("/api/fees", methods=["GET"])
def get_fees():
    try:
        res = requests.get(f"{BASE_URL}/fee", timeout=8)
        res.raise_for_status()
        return jsonify(res.json())
    except Exception as e:
        return jsonify({"error": "Failed to fetch fees", "details": str(e)}), 500


@app.route("/api/exchange", methods=["GET"])
def get_exchange():
    from_currency = request.args.get("from", "USD")
    to_currency = request.args.get("to", "NGN")

    now = time.time()
    if FX_CACHE["data"] and now - FX_CACHE["timestamp"] < 300:
        return jsonify(FX_CACHE["data"])

    try:
        res = requests.get(
            f"{BASE_URL}/exchange",
            params={"from": from_currency, "to": to_currency},
            timeout=8
        )
        res.raise_for_status()
        FX_CACHE["data"] = res.json()
        FX_CACHE["timestamp"] = now
        return jsonify(FX_CACHE["data"])
    except Exception as e:
        return jsonify({"error": "Failed to fetch exchange rate", "details": str(e)}), 500


@app.route("/api/simulate", methods=["POST"])
def simulate_transaction():
    """
    Body:
    {
      "amount": 100,
      "from": "USD",
      "to": "NGN",
      "swap_tag": "TEAMEX"
    }
    """
    data = request.get_json() or {}
    amount = float(data.get("amount", 0))
    from_currency = data.get("from", "USD")
    to_currency = data.get("to", "NGN")
    swap_tag = data.get("swap_tag", "N/A")

    if amount <= 0:
        return jsonify({"error": "Amount must be greater than zero"}), 400

    # 1️⃣ Fetch FX rate
    fx_res = requests.get(f"{BASE_URL}/exchange", params={"from": from_currency, "to": to_currency})
    fx_data = fx_res.json()
    fx_rate = fx_data.get("rate", 1)

    # 2️⃣ Fetch fee data
    fee_res = requests.get(f"{BASE_URL}/fee")
    fee_data = fee_res.json()

    customer_data = fee_data.get("Customer", {}).get("products", {})
    if not customer_data:
        return jsonify({"error": "No fee data found"}), 500

    first_product = next(iter(customer_data.values()))
    service = first_product["services"][0]

    percent_fee = float(service["rate"])
    fixed_fee = float(service["min"])
    total_fee = (percent_fee * amount) + fixed_fee
    net_amount = amount - total_fee
    converted_amount = round(net_amount * fx_rate, 2)
    referral_bonus = round(total_fee * REFERRAL_BONUS_RATE, 2)

    # 3️⃣ Store in PostgreSQL
    db: Session = SessionLocal()
    new_referral = Referral(
        swap_tag=swap_tag,
        amount=amount,
        fee_collected=total_fee,
        referral_bonus=referral_bonus,
        from_currency=from_currency,
        to_currency=to_currency,
        exchange_rate=fx_rate,
        converted_amount=converted_amount,
    )
    db.add(new_referral)
    db.commit()
    db.refresh(new_referral)
    db.close()

    # 4️⃣ Return response
    return jsonify({
        "input": {
            "amount": amount,
            "from": from_currency,
            "to": to_currency,
            "swap_tag": swap_tag
        },
        "exchange_rate": fx_rate,
        "fees": {
            "percent_fee": percent_fee,
            "fixed_fee": fixed_fee,
            "total_fee": total_fee
        },
        "converted_amount": converted_amount,
        "referral": {
            "bonus": referral_bonus,
            "rate_percent": REFERRAL_BONUS_RATE * 100
        }
    })


@app.route("/api/referrals", methods=["GET"])
def get_referrals():
    """Retrieve all stored referral transactions from PostgreSQL"""
    db: Session = SessionLocal()
    records = db.query(Referral).all()
    db.close()

    return jsonify({
        "count": len(records),
        "referrals": [
            {
                "id": r.id,
                "swap_tag": r.swap_tag,
                "amount": r.amount,
                "fee_collected": r.fee_collected,
                "referral_bonus": r.referral_bonus,
                "from_currency": r.from_currency,
                "to_currency": r.to_currency,
                "exchange_rate": r.exchange_rate,
                "converted_amount": r.converted_amount,
                "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            }
            for r in records
        ]
    })


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
