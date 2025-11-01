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

# ----------------------------
# ROUTES
# ----------------------------

@app.route("/")
def index():
    return jsonify({
        "message": "VitalSwap Fee Page Backend is running",
        "endpoints": ["/api/fees", "/api/exchange", "/api/simulate"]
    })

@app.route("/api/fees", methods=["GET"])
def get_fees():
    """Fetch current fees from VitalSwap API"""
    try:
        response = requests.get(f"{BASE_URL}/fee", timeout=8)
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": "Failed to fetch fees", "details": str(e)}), 500


@app.route("/api/exchange", methods=["POST"])
def get_exchange_rate():
    """Fetch USD↔NGN exchange rate from VitalSwap API"""
    from_currency = request.args.get("from", "USD")
    to_currency = request.args.get("to", "NGN")#

   try:
        response = requests.get(
            f"{BASE_URL}/exchange",
           params={"from": from_currency, "to": to_currency},
           timeout=8
        )
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
       return jsonify({"error": "Failed to fetch exchange rate", "details": str(e)}), 500

#@app.route("/api/exchange", methods=["POST"])
#def get_exchange_rate():
#    from_currency = request.args.get("from", "USD")
#    to_currency = request.args.get("to", "NGN")
#    try:
#        response = requests.get(f"{BASE_URL}/exchange", params={"from": from_currency, "to": to_currency}, timeout=8)
#        response.raise_for_status()
#        data = response.json()
#    except Exception:
#        # fallback local data to prevent frontend errors
#        data = {
#            "USD": {"NGN": 1480, "EUR": 0.93},
#            "NGN": {"USD": 0.0012, "EUR": 0.0011},
#            "EUR": {"USD": 1.08, "NGN": 1700}
#        }
#    return jsonify(data)



@app.route("/api/simulate", methods=["POST"])
def simulate_transaction():
    """
    Combines /fee and /exchange results into one unified calculator output.
    Expected body: { "amount": 100, "from": "USD", "to": "NGN" }
    """
    data = request.get_json()
    amount = float(data.get("amount", 0))
    from_currency = data.get("from", "USD")
    to_currency = data.get("to", "NGN")

    # Fetch current exchange rate
    fx_res = requests.get(
        f"{BASE_URL}/exchange", params={"from": from_currency, "to": to_currency}
    ).json()
    rate = fx_res.get("rate", 1)

    # Example: Apply a fixed fee rule from the /fee endpoint
    fee_data = requests.get(f"{BASE_URL}/fee").json()
    customer_fees = fee_data.get("Customer", {}).get("products", {})
    first_product = next(iter(customer_fees.values()))
    first_service = first_product["services"][0]
    percent_fee = float(first_service["rate"])
    fixed_fee = float(first_service["min"])

    total_fee = (percent_fee * amount) + fixed_fee
    converted_amount = (amount - total_fee) * rate

    return jsonify({
        "input": {"amount": amount, "from": from_currency, "to": to_currency},
        "exchange_rate": rate,
        "fee_details": {
            "percent_fee": percent_fee,
            "fixed_fee": fixed_fee,
            "total_fee": total_fee
        },
        "converted_amount": converted_amount
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
