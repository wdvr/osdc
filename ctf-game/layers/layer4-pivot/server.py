"""
Layer 4: Pivot Server
HTTP service that acts as a gateway to the final agent challenge.
Requires authentication key from Layer 3 PRIZE.txt.
"""

import os
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

PIVOT_KEY = os.environ.get("PIVOT_KEY", "ESCALATION-COMPLETE-2024")
AGENT_ENDPOINT = os.environ.get("AGENT_ENDPOINT", "http://layer5-agent:6666")


INDEX_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Internal Gateway</title>
    <style>
        body { font-family: monospace; background: #1a1a1a; color: #0f0; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        .error { color: #f00; }
        .success { color: #0f0; }
        pre { background: #000; padding: 15px; border: 1px solid #333; }
        h1 { border-bottom: 1px solid #333; padding-bottom: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Internal Gateway Service</h1>
        <p>Welcome to the internal network pivot point.</p>
        <p>This service provides access to internal resources.</p>

        <h2>Authentication Required</h2>
        <p>Send requests with the <code>X-Pivot-Key</code> header to access protected resources.</p>

        <h2>Available Endpoints</h2>
        <pre>
GET  /status     - Service status (public)
GET  /internal   - Internal network info (requires auth)
GET  /agent      - Access the AI agent (requires auth)
POST /agent      - Send message to AI agent (requires auth)
        </pre>

        <p class="error">Note: Unauthorized access attempts are logged.</p>
    </div>
</body>
</html>
"""


def check_auth():
    """Check if request has valid pivot key"""
    key = request.headers.get("X-Pivot-Key", "")
    return key == PIVOT_KEY


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/status")
def status():
    return jsonify({
        "service": "Internal Gateway",
        "status": "operational",
        "auth_required": True,
        "hint": "You need the X-Pivot-Key header from the previous layer"
    })


@app.route("/internal")
def internal():
    if not check_auth():
        return jsonify({
            "error": "Unauthorized",
            "message": "Valid X-Pivot-Key header required"
        }), 401

    return jsonify({
        "status": "authenticated",
        "flag": "FLAG{p1v0t_p01nt_d1sc0v3r3d}",
        "internal_services": [
            {"name": "layer5-agent", "port": 6666, "description": "AI Assistant"},
        ],
        "next_step": {
            "description": "Access the AI agent to retrieve the final secret",
            "endpoint": "/agent",
            "method": "POST",
            "body": {"message": "your message to the agent"}
        }
    })


@app.route("/agent", methods=["GET"])
def agent_info():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    return jsonify({
        "agent": "Corporate AI Assistant",
        "version": "2.0",
        "description": "An AI assistant that helps with internal queries",
        "usage": {
            "method": "POST",
            "endpoint": "/agent",
            "body": {"message": "your question"},
            "example": "curl -X POST -H 'X-Pivot-Key: ...' -H 'Content-Type: application/json' -d '{\"message\": \"hello\"}' http://layer4-pivot:7777/agent"
        },
        "note": "The agent has access to sensitive information but is instructed not to reveal it. Can you convince it otherwise?"
    })


@app.route("/agent", methods=["POST"])
def agent_chat():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({
            "error": "Missing message",
            "usage": {"body": {"message": "your question"}}
        }), 400

    message = data["message"]

    # Forward to the actual agent service
    import requests
    try:
        resp = requests.post(
            f"{AGENT_ENDPOINT}/chat",
            json={"message": message},
            timeout=30
        )
        return jsonify(resp.json())
    except requests.exceptions.ConnectionError:
        return jsonify({
            "error": "Agent service unavailable",
            "hint": "The agent service might not be running yet"
        }), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/hint")
def hint():
    return jsonify({
        "hints": [
            "The X-Pivot-Key was revealed in the PRIZE.txt from Layer 3",
            "Once authenticated, check /internal for the flag and next steps",
            "The final challenge involves convincing an AI agent to reveal a secret"
        ]
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7777, debug=False)
