import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def connect_to_db():
    try:
        # Establish a connection
        conn = psycopg2.connect(DATABASE_URL)
        print("✅ Connected to PostgreSQL database successfully!")

        # Create a cursor object
        cur = conn.cursor()

        # Example query to verify connection
        cur.execute("SELECT version();")
        db_version = cur.fetchone()
        print("Database version:", db_version)

        # Close cursor and connection
        cur.close()
        conn.close()
        print("🔒 Connection closed.")
    except Exception as e:
        print("❌ Database connection failed:", e)

if __name__ == "__main__":
    connect_to_db()
