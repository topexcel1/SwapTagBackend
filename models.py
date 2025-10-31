from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey
from datetime import datetime
from database import Base

class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    swap_tag = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    fee_collected = Column(Float, nullable=False)
    referral_bonus = Column(Float, nullable=False)
    from_currency = Column(String, default="USD")
    to_currency = Column(String, default="NGN")
    exchange_rate = Column(Float)
    converted_amount = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    swap_tag = Column(String, nullable=True, index=True)   # optional: connect chat to a swap_tag / user
    role = Column(String, nullable=False)                  # "user" or "assistant" or "system"
    content = Column(Text, nullable=False)
    metadata = Column(String, nullable=True)               # short JSON string or small metadata
    timestamp = Column(DateTime, default=datetime.utcnow)
