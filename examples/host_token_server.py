"""What a CUSTOMER adds to THEIR OWN backend so SupportPilot knows who the signed-in user is.

This is the only integration step that matters for security: order lookups are authorised by the `sub`
in this token, which only your server (holding the shared secret) can mint. Flask shown; any framework works.

    pip install flask pyjwt
    SUPPORTPILOT_SECRET=<value of the tenant's user_token_secret_env> flask --app host_token_server run
"""
import os
import time

import jwt
from flask import Flask, jsonify, session

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-me")
SECRET = os.environ["SUPPORTPILOT_SECRET"]


@app.get("/api/support-token")
def support_token():
    user = session.get("user")  # <- YOUR existing authentication
    if not user:
        return jsonify(error="not signed in"), 401  # the widget falls back to anonymous chat
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": str(user["id"]),          # REQUIRED: the id your order system uses for this customer
            "name": user.get("name"),
            "email": user.get("email"),      # used for tickets so the bot doesn't have to ask
            "aud": "supportpilot",           # REQUIRED, must be exactly this
            "iat": now,
            "exp": now + 300,                # REQUIRED, keep it short (max 1 hour is enforced)
        },
        SECRET,
        algorithm="HS256",
    )
    return jsonify(user_token=token)
