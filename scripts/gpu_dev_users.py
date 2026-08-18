#!/usr/bin/env python3
"""Per-user gpu-dev usage over a trailing window (default 90 days).

Reads the reservations table in every region that has one (read-only scan) and
credits each reservation only for the part of its lifetime inside the window.

Usage: uv run python scripts/gpu_dev_users.py [--days 90]
Writes gpu_dev_users.json next to itself.
"""
import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import boto3

from gpu_dev_monthly_report import CPU_TYPES, normalize_type, parse_ts

# us-west-1 is the staging cluster (environment=test), kept separate from prod totals.
REGIONS = {"us-east-2": "prod", "us-east-1": "prod-spot", "us-west-1": "staging"}
TABLE = "pytorch-gpu-dev-reservations"
FIELDS = ("reservation_id,gpu_type,gpu_count,#s,launched_at,expires_at,cancelled_at,"
          "expired_at,failed_at,reservation_ended,user_id,github_user,instance_type")


def scan(region):
    ddb = boto3.client("dynamodb", region_name=region)
    items, kw = [], {}
    while True:
        r = ddb.scan(TableName=TABLE, ProjectionExpression=FIELDS,
                     ExpressionAttributeNames={"#s": "status"}, **kw)
        items += [{k: list(v.values())[0] for k, v in it.items()} for it in r["Items"]]
        if "LastEvaluatedKey" not in r:
            return items
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def interval(it, now):
    """-> (start, end) of the reservation's live window, or None."""
    start = parse_ts(it.get("launched_at"))
    if not start:
        return None
    end = next((parse_ts(it[f]) for f in
                ("reservation_ended", "cancelled_at", "expired_at", "failed_at")
                if parse_ts(it.get(f))), None)
    if not end:
        end = now if it.get("status") == "active" else parse_ts(it.get("expires_at"))
    if not end:
        return None
    end = min(end, now)
    return (start, end) if end > start else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lo = now - timedelta(days=args.days)

    users = defaultdict(lambda: defaultdict(float))
    last_seen = {}
    meta_ids = defaultdict(Counter)   # github user -> {meta unixname: records}
    skipped = defaultdict(int)
    for region, env in REGIONS.items():
        for it in scan(region):
            iv = interval(it, now)
            if not iv:
                skipped[f"{region}:no_interval"] += 1
                continue
            s, e = max(iv[0], lo), min(iv[1], now)
            if e <= s:
                continue
            hours = (e - s).total_seconds() / 3600
            cat, _, _ = normalize_type(it.get("gpu_type"), it.get("instance_type"))
            try:
                gc = int(it.get("gpu_count") or 0)
            except (TypeError, ValueError):
                gc = 0
            # user_id is an SSO identity (<unixname>@meta.com) for Meta employees; external
            # contributors (NVIDIA etc.) have no @meta.com id, only a GitHub login.
            uid = str(it.get("user_id") or "")
            unix = uid.split("@")[0].lower() if uid.endswith("@meta.com") else None
            user = str(it.get("github_user") or unix or uid or "(unknown)").lower()
            if unix:
                meta_ids[user][unix] += 1
            u = users[user]
            u["reservations"] += 1
            u["wall_hours"] += hours
            u[f"env_{env}_hours"] += hours
            if cat in CPU_TYPES:
                u["cpu_node_hours"] += hours
            else:
                u["gpu_hours"] += gc * hours
                u[f"type_{cat}"] += gc * hours
            last_seen[user] = max(last_seen.get(user, e), e)

    rows = []
    for user, u in users.items():
        types = {k[5:]: round(v, 1) for k, v in u.items() if k.startswith("type_")}
        ids = meta_ids[user].most_common()
        rows.append({
            "user": user,
            "meta_user": ids[0][0] if ids else None,
            "meta_user_alts": [n for n, _ in ids[1:]],
            "gpu_hours": round(u["gpu_hours"], 1),
            "cpu_node_hours": round(u["cpu_node_hours"], 1),
            "wall_hours": round(u["wall_hours"], 1),
            "reservations": int(u["reservations"]),
            "last_active": last_seen[user].strftime("%Y-%m-%d"),
            "top_types": dict(sorted(types.items(), key=lambda kv: -kv[1])[:4]),
            "envs": {k[4:-6]: round(v, 1) for k, v in u.items()
                     if k.startswith("env_") and v > 0},
        })
    rows.sort(key=lambda r: (-r["gpu_hours"], -r["cpu_node_hours"]))

    out = {"generated_at": now.isoformat() + "Z",
           "window": {"start": lo.isoformat(), "end": now.isoformat(), "days": args.days},
           "regions": REGIONS, "skipped": dict(skipped),
           "distinct_users": len(rows),
           "total_gpu_hours": round(sum(r["gpu_hours"] for r in rows), 1),
           "total_cpu_node_hours": round(sum(r["cpu_node_hours"] for r in rows), 1),
           "users": rows}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_dev_users.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)

    print(f"window {lo:%Y-%m-%d} .. {now:%Y-%m-%d}  users={len(rows)}  "
          f"gpu_h={out['total_gpu_hours']:,.0f}  cpu_node_h={out['total_cpu_node_hours']:,.0f}")
    print(f"{'#':>3} {'github_user':<22} {'meta_user':<18} {'gpu-h':>9} {'cpu-h':>8} "
          f"{'wall-h':>8} {'resv':>5} {'last':>10}  top types")
    for i, r in enumerate(rows, 1):
        t = ", ".join(f"{k} {v:g}" for k, v in r["top_types"].items())
        print(f"{i:>3} {r['user']:<22} {(r['meta_user'] or '-- external --'):<18} "
              f"{r['gpu_hours']:>9,.1f} {r['cpu_node_hours']:>8,.1f} "
              f"{r['wall_hours']:>8,.1f} {r['reservations']:>5} {r['last_active']:>10}  {t}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
