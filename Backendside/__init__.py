from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import requests, os, time
from database import Base, engine, SessionLocal
from models import Referral

# Load .env variables
load_dotenv()

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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
