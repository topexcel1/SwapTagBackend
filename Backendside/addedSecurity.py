import os
from flask import abort
ADMIN_KEY = os.getenv("ADMIN_KEY", "supersecret")

@app.route("/api/stats", methods=["GET"])
def get_stats():
    key = request.headers.get("X-Admin-Key")
    if key != ADMIN_KEY:
        abort(401, description="Unauthorized access")
    