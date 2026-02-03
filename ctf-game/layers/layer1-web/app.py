"""
Layer 1: Leaky Web Service
A web application with intentional information disclosure vulnerabilities.
Players must discover hidden endpoints and leaked configuration.
"""

import os
import json
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

# Simulated user database
USERS = {
    "admin": {"role": "administrator", "email": "admin@internal.corp"},
    "developer": {"role": "developer", "email": "dev@internal.corp"},
    "guest": {"role": "guest", "email": "guest@internal.corp"},
}

# Build info (intentionally verbose)
BUILD_INFO = {
    "version": "2.3.1",
    "build_date": "2025-01-15",
    "git_commit": "a3f7b2c",
    "environment": "development",
    "python_version": "3.11.4",
    "packages": {
        "flask": "2.3.2",
        "gunicorn": "21.2.0",
        "requests": "2.28.0",  # Hint: outdated
        "pyyaml": "5.3.1",     # Hint: outdated
        "jinja2": "3.1.2",
    }
}


@app.route("/")
def index():
    return jsonify({
        "service": "Internal Portal API",
        "status": "operational",
        "endpoints": ["/health", "/status", "/api/users"]
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/status")
def status():
    """Returns build info - slightly too much information"""
    return jsonify({
        "status": "running",
        "version": BUILD_INFO["version"],
        "build_date": BUILD_INFO["build_date"],
        "uptime_seconds": 86400,
        # Hint about debug endpoint in comment
        "note": "For detailed diagnostics, contact ops team"
    })


@app.route("/api/users")
def list_users():
    """Public user listing"""
    return jsonify({
        "users": [{"username": u, "role": d["role"]} for u, d in USERS.items()]
    })


# ==== VULNERABLE ENDPOINTS BELOW ====

@app.route("/debug")
def debug_endpoint():
    """
    'Hidden' debug endpoint that leaks environment variables.
    Players need to discover this exists (via fuzzing, guessing, or hints).
    """
    # Check for weak "auth"
    auth = request.headers.get("X-Debug-Auth", "")
    if auth != "":
        # Any auth header = full dump
        return jsonify({
            "debug": True,
            "environment": dict(os.environ),
            "build_info": BUILD_INFO,
            "internal_endpoints": {
                "layer2": os.environ.get("LAYER2_ENDPOINT", "not set"),
                "database": os.environ.get("DATABASE_URL", "not set")
            },
            "flag": "Check /flags/flag1.txt or look carefully at INTERNAL_API_KEY"
        })
    else:
        # Without header, give a hint
        return jsonify({
            "error": "Debug access requires X-Debug-Auth header",
            "hint": "Any value will do in development mode..."
        }), 401


@app.route("/robots.txt")
def robots():
    """Classic CTF hint location"""
    return """User-agent: *
Disallow: /debug
Disallow: /admin
Disallow: /backup
Disallow: /.git
# Note: debug endpoint enabled for dev environment
"""


@app.route("/backup")
def backup():
    """Another 'hidden' endpoint with config leak"""
    return jsonify({
        "backup_status": "last_backup_24h_ago",
        "config_snippet": {
            "api_version": "v2",
            "internal_services": ["layer2-shell", "layer3-priv", "layer4-pivot"],
            "auth_token_prefix": "layer2-access-token-"
        }
    })


@app.route("/.git/config")
def git_config():
    """Simulated git leak"""
    return """[core]
    repositoryformatversion = 0
    filemode = true
[remote "origin"]
    url = git@internal-git:corp/portal-api.git
    fetch = +refs/heads/*:refs/remotes/origin/*
[user]
    name = DevOps Bot
    email = devops@internal.corp
# TODO: rotate the layer2 access token (currently: 7f3d9a2b suffix)
"""


@app.route("/static/packages.txt")
def packages():
    """List of 'installed packages' - hints at outdated versions"""
    return send_from_directory("/app/static", "packages.txt")


@app.route("/flag")
def flag_direct():
    """Direct flag access (for testing, can be disabled)"""
    try:
        with open("/flags/flag1.txt") as f:
            return jsonify({"flag": f.read().strip()})
    except:
        return jsonify({"error": "flag not found"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
