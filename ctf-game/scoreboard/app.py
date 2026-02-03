"""
CTF Scoreboard - Flag Submission and Progress Tracking
"""

import os
import json
from datetime import datetime
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)

# Load flags from environment
FLAGS = {
    1: os.environ.get("FLAG1", "FLAG{1nf0_d1scl0sur3_1s_r34l}"),
    2: os.environ.get("FLAG2", "FLAG{r3str1ct3d_sh3ll_3sc4p3}"),
    3: os.environ.get("FLAG3", "FLAG{pr1v1l3g3_3sc4l4t10n_m4st3r}"),
    4: os.environ.get("FLAG4", "FLAG{p1v0t_p01nt_d1sc0v3r3d}"),
    5: os.environ.get("FLAG5", "FLAG{pr0mpt_1nj3ct10n_m4st3r_h4ck3r}"),
}

LAYER_NAMES = {
    1: "Web Service - Information Disclosure",
    2: "Restricted Shell - Escape",
    3: "Privilege Escalation",
    4: "Pivot Gateway",
    5: "AI Agent - Prompt Injection",
}

LAYER_POINTS = {
    1: 100,
    2: 200,
    3: 300,
    4: 200,
    5: 400,
}

# In-memory storage (would use DB in production)
submissions = []
captured_flags = set()
start_time = None


def get_state():
    """Get current game state"""
    return {
        "captured": list(captured_flags),
        "total_layers": 5,
        "total_points": sum(LAYER_POINTS[f] for f in captured_flags),
        "max_points": sum(LAYER_POINTS.values()),
        "submissions_count": len(submissions),
        "game_started": start_time is not None,
        "start_time": start_time.isoformat() if start_time else None,
    }


@app.route("/")
def index():
    """Main scoreboard page"""
    state = get_state()
    layers = []
    for i in range(1, 6):
        layers.append({
            "number": i,
            "name": LAYER_NAMES[i],
            "points": LAYER_POINTS[i],
            "captured": i in captured_flags,
        })

    return render_template("index.html", state=state, layers=layers)


@app.route("/api/status")
def api_status():
    """API endpoint for game status"""
    return jsonify(get_state())


@app.route("/api/submit", methods=["POST"])
def api_submit():
    """Submit a flag"""
    global start_time

    if start_time is None:
        start_time = datetime.now()

    data = request.get_json()
    if not data or "flag" not in data:
        return jsonify({"error": "Missing 'flag' field"}), 400

    flag = data["flag"].strip()
    team = data.get("team", "anonymous")

    # Find which layer this flag belongs to
    layer_found = None
    for layer_num, layer_flag in FLAGS.items():
        if flag == layer_flag:
            layer_found = layer_num
            break

    submission = {
        "timestamp": datetime.now().isoformat(),
        "team": team,
        "flag": flag[:20] + "..." if len(flag) > 20 else flag,
        "correct": layer_found is not None,
        "layer": layer_found,
    }
    submissions.append(submission)

    if layer_found is None:
        return jsonify({
            "success": False,
            "message": "Invalid flag. Keep trying!",
        })

    if layer_found in captured_flags:
        return jsonify({
            "success": True,
            "message": f"Flag already captured for Layer {layer_found}!",
            "layer": layer_found,
            "points": 0,
            "already_captured": True,
        })

    captured_flags.add(layer_found)

    return jsonify({
        "success": True,
        "message": f"Correct! Layer {layer_found} ({LAYER_NAMES[layer_found]}) captured!",
        "layer": layer_found,
        "points": LAYER_POINTS[layer_found],
        "total_points": sum(LAYER_POINTS[f] for f in captured_flags),
        "layers_remaining": 5 - len(captured_flags),
    })


@app.route("/api/submissions")
def api_submissions():
    """Get submission history"""
    return jsonify(submissions[-50:])  # Last 50


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset the game (admin only)"""
    global submissions, captured_flags, start_time

    # Simple auth check
    auth = request.headers.get("X-Admin-Key", "")
    if auth != "ctf-admin-reset-2024":
        return jsonify({"error": "Unauthorized"}), 401

    submissions = []
    captured_flags = set()
    start_time = None

    return jsonify({"message": "Game reset successfully"})


@app.route("/api/layers")
def api_layers():
    """Get layer information"""
    layers = []
    for i in range(1, 6):
        layers.append({
            "number": i,
            "name": LAYER_NAMES[i],
            "points": LAYER_POINTS[i],
            "captured": i in captured_flags,
            "hint": f"Target for Layer {i}" if i not in captured_flags else "Completed!",
        })
    return jsonify(layers)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
