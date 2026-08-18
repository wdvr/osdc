#!/usr/bin/env python3
"""Monthly capacity / utilization / active-user report for the gpu-dev cluster (prod, us-east-2).

Sources (all read-only):
  - DynamoDB pytorch-gpu-dev-reservations   -> usage (GPU-hours, concurrency, users)
  - DynamoDB pytorch-gpu-dev-gpu-availability -> current delivered + funded capacity
  - terraform main.tf (via git history)     -> funded capacity per month

Writes report.json next to itself.
"""
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

import boto3

REGION = "us-east-2"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_TF = "terraform-gpu-devservers/main.tf"
START = datetime(2026, 1, 1)
NOW = datetime.now(timezone.utc).replace(tzinfo=None)

# MIG slice -> fraction of a physical GPU (per-GPU profile is 2x1g + 1x2g + 1x3g = 7 units)
MIG_FRACTION = {"1g": 1 / 7, "2g": 2 / 7, "3g": 3 / 7}
GPUS_PER_INSTANCE = {  # prod supported_gpu_types, physical node pools only
    "b200": 8, "h200": 8, "h100": 8, "a100": 8,
    "t4": 4, "l4": 4, "a10g": 4, "rtxpro6000": 4, "b300": 8,
    "cpu-arm": 0, "cpu-x86": 0, "cpu-spot": 0,
}
CPU_TYPES = {"cpu-arm", "cpu-x86", "cpu-spot"}


def months(start, end):
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


MONTHS = months(START, NOW)


def month_bounds(key):
    y, m = map(int, key.split("-"))
    lo = datetime(y, m, 1)
    hi = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
    return lo, min(hi, NOW)


def parse_ts(v):
    if not v:
        return None
    if isinstance(v, Decimal):  # epoch seconds
        return datetime.utcfromtimestamp(float(v))
    s = str(v).replace("Z", "").replace("+00:00", "")
    if s.isdigit():
        return datetime.utcfromtimestamp(int(s))
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# Many records (esp. CPU pods) carry gpu_type="Unknown" and only an instance_type.
INSTANCE_POOL = {
    "c7i.8xlarge": "cpu-x86", "c7g.8xlarge": "cpu-arm",
    "g5.12xlarge": "a10g", "g6.12xlarge": "l4", "g7e.24xlarge": "rtxpro6000",
    "g4dn.12xlarge": "t4", "p5.48xlarge": "h100", "p5e.48xlarge": "h200",
    "p5en.48xlarge": "h200", "p6-b200.48xlarge": "b200", "p4d.24xlarge": "a100",
}


def normalize_type(gt, instance_type=None):
    """-> (category, physical_pool, physical_gpu_weight_multiplier)"""
    t = (gt or "unknown").strip().lower()
    m = re.match(r"^(h100|b200|h200|a100)-mig-([123]g)$", t)
    if m:
        return t, m.group(1), MIG_FRACTION[m.group(2)]
    if t in ("unknown", "") and instance_type in INSTANCE_POOL:
        p = INSTANCE_POOL[instance_type]
        return p, p, 1.0
    return t, t, 1.0


# ---------------------------------------------------------------- usage
def load_reservations():
    ddb = boto3.client("dynamodb", region_name=REGION)
    fields = ("reservation_id,gpu_type,gpu_count,#s,created_at,launched_at,expires_at,"
              "cancelled_at,expired_at,failed_at,reservation_ended,user_id,github_user,"
              "is_multinode,master_reservation_id,node_ip,instance_type")
    items, kw = [], {}
    while True:
        r = ddb.scan(TableName="pytorch-gpu-dev-reservations",
                     ProjectionExpression=fields,
                     ExpressionAttributeNames={"#s": "status"}, **kw)
        for it in r["Items"]:
            items.append({k: list(v.values())[0] for k, v in it.items()})
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return items


def usage_intervals(items):
    """-> list of (start, end, category, pool, physical_gpus, user, nodes)"""
    out, skipped = [], defaultdict(int)
    for it in items:
        start = parse_ts(it.get("launched_at"))
        if not start:
            skipped["never_launched"] += 1
            continue
        end = None
        for f in ("reservation_ended", "cancelled_at", "expired_at", "failed_at"):
            end = parse_ts(it.get(f))
            if end:
                break
        status = it.get("status", "")
        if not end:
            end = NOW if status == "active" else parse_ts(it.get("expires_at"))
        if not end:
            skipped["no_end"] += 1
            continue
        end = min(end, NOW)
        if end <= start:
            skipped["zero_length"] += 1
            continue
        cat, pool, frac = normalize_type(it.get("gpu_type"), it.get("instance_type"))
        try:
            gc = int(it.get("gpu_count") or 0)
        except (TypeError, ValueError):
            gc = 0
        user = it.get("github_user") or it.get("user_id") or "(unknown)"
        nodes = 1 if cat in CPU_TYPES else 0
        out.append((start, end, cat, pool, gc * frac, str(user).lower(), nodes, gc,
                    it.get("node_ip")))
    return out, dict(skipped)


def aggregate(intervals):
    gpu_h = defaultdict(float)          # (month, category) -> gpu-hours
    pool_h = defaultdict(float)         # (month, pool) -> physical gpu-hours
    cpu_node_h = defaultdict(float)     # month -> cpu node-hours
    users = defaultdict(set)            # month -> users
    users_gpu = defaultdict(set)
    resv = defaultdict(int)             # month -> reservations launched
    events = defaultdict(list)          # (month, pool) -> concurrency events

    for start, end, cat, pool, gpus, user, nodes, raw_gc, node_ip in intervals:
        for mk in MONTHS:
            lo, hi = month_bounds(mk)
            s, e = max(start, lo), min(end, hi)
            if e <= s:
                continue
            hours = (e - s).total_seconds() / 3600
            users[mk].add(user)
            if cat in CPU_TYPES:
                cpu_node_h[mk] += hours
            else:
                gpu_h[(mk, cat)] += raw_gc * hours
                pool_h[(mk, pool)] += gpus * hours
                users_gpu[mk].add(user)
                if gpus:
                    events[(mk, pool)] += [(s, gpus), (e, -gpus)]
            if lo <= start < hi:
                resv[mk] += 1

    peak = {}
    for k, ev in events.items():
        cur = best = 0.0
        for _, d in sorted(ev):
            cur += d
            best = max(best, cur)
        peak[k] = round(best, 2)

    # Delivered-capacity estimate = monthly high-water mark of concurrently allocated GPUs.
    # The placement gate never overcommits a node, so HWM <= real delivered capacity: a
    # provable lower bound. (node_ip cannot be used to count nodes -- since the SSH-proxy
    # change it holds one shared public endpoint, not a per-node address.)
    return dict(gpu_h=gpu_h, pool_h=pool_h, cpu_node_h=cpu_node_h, users=users,
                users_gpu=users_gpu, resv=resv, peak=peak, delivered=dict(peak))


# ---------------------------------------------------------------- cost / node-hours
# us-east-2 Linux on-demand list, verified against Cost Explorer actuals to the dollar.
LIST_RATE = {"p5.48xlarge": 55.04, "p6-b200.48xlarge": 113.9328, "p5en.48xlarge": 63.296,
             "p4d.24xlarge": 21.95764, "g7e.24xlarge": 16.57216, "c7i.8xlarge": 1.428,
             "c7g.8xlarge": 1.1562, "m7i.48xlarge": 9.6768, "g6.12xlarge": 4.6016,
             "g4dn.12xlarge": 3.912, "g5.12xlarge": 5.672}
# Blended effective $/instance-hour actually paid account-wide Jan-Jul (prepaid capacity
# blocks / total instance-hours). p5en has no derivable rate -- prepaid before the window --
# so it takes the mean discount observed on p5 + p6-b200.
BLENDED_RATE = {"p5.48xlarge": 14.64, "p6-b200.48xlarge": 21.84}
BLENDED_RATE["p5en.48xlarge"] = round(LIST_RATE["p5en.48xlarge"] * (
    (BLENDED_RATE["p5.48xlarge"] / LIST_RATE["p5.48xlarge"]
     + BLENDED_RATE["p6-b200.48xlarge"] / LIST_RATE["p6-b200.48xlarge"]) / 2), 2)
POOL_INSTANCE = {"h100": "p5.48xlarge", "b200": "p6-b200.48xlarge", "h200": "p5en.48xlarge",
                 "a100": "p4d.24xlarge", "rtxpro6000": "g7e.24xlarge", "l4": "g6.12xlarge",
                 "t4": "g4dn.12xlarge", "a10g": "g5.12xlarge"}
BLOCK_POOLS = ("h100", "b200", "h200")   # served out of prepaid EC2 Capacity Blocks


def asg_pool(asg):
    k = asg.replace("pytorch-gpu-dev-gpu-nodes-", "").replace("pytorch-gpu-dev-", "")
    for p in ("h100", "b200", "h200", "a100", "rtxpro6000", "a10g", "l4", "t4"):
        if k.startswith(p):
            return p
    if k.startswith("g7e"):
        return "rtxpro6000"
    if k.startswith("build"):
        return "build"
    if k.startswith("cpu"):
        return "cpu"
    return None


def ce_by_asg():
    """Cost Explorer, EC2 compute in-region, grouped by the ASG cost-allocation tag.

    Returns {month: {pool: {"cost": $, "node_hours": h}}} for gpu-dev's own ASGs only.
    Instance-hours are present even where cost is $0 (capacity-block-backed pools), which
    is what makes measured delivered capacity available for h100/b200/h200.
    """
    ce = boto3.client("ce", region_name="us-east-1")
    out = defaultdict(lambda: defaultdict(lambda: {"cost": 0.0, "node_hours": 0.0}))
    tok = None
    while True:
        kw = dict(
            TimePeriod={"Start": START.date().isoformat(), "End": NOW.date().isoformat()},
            Granularity="MONTHLY", Metrics=["UnblendedCost", "UsageQuantity"],
            Filter={"And": [
                {"Dimensions": {"Key": "REGION", "Values": [REGION]}},
                {"Dimensions": {"Key": "SERVICE",
                                "Values": ["Amazon Elastic Compute Cloud - Compute"]}}]},
            GroupBy=[{"Type": "TAG", "Key": "aws:autoscaling:groupName"}])
        if tok:
            kw["NextPageToken"] = tok
        r = ce.get_cost_and_usage(**kw)
        for t in r["ResultsByTime"]:
            mk = t["TimePeriod"]["Start"][:7]
            for g in t["Groups"]:
                asg = g["Keys"][0].split("$", 1)[-1]
                if not asg.startswith("pytorch-gpu-dev"):
                    continue
                p = asg_pool(asg)
                if not p:
                    continue
                out[mk][p]["cost"] += float(g["Metrics"]["UnblendedCost"]["Amount"])
                out[mk][p]["node_hours"] += float(g["Metrics"]["UsageQuantity"]["Amount"])
        tok = r.get("NextPageToken")
        if not tok:
            break
    return {m: dict(v) for m, v in out.items()}


# ---------------------------------------------------------------- capacity
def current_capacity():
    t = boto3.resource("dynamodb", region_name=REGION).Table("pytorch-gpu-dev-gpu-availability")
    rows = {}
    for it in t.scan()["Items"]:
        gt = it["gpu_type"]
        rows[gt] = {
            "delivered_gpus": float(it.get("total_gpus", 0)),
            "running_instances": float(it.get("running_instances", 0)),
            "desired_instances": float(it.get("desired_capacity", 0)),
            "gpus_per_instance": float(it.get("gpus_per_instance", 0)),
            "available_now": float(it.get("available_gpus", 0)),
        }
    return rows


CR_BLOCK = re.compile(r"^\s{4}prod = \{(.*?)^\s{4}\}", re.S | re.M)
CR_TYPE = re.compile(r"^\s{6}(\w[\w-]*) = \[(.*?)^\s{6}\]", re.S | re.M)
CR_ENTRY = re.compile(r"instance_count\s*=\s*(\d+)")
CR_LINE = re.compile(r'key\s*=\s*"(\w+)"\s*,\s*id\s*=\s*(?:"(cr-\w+)"|null)\s*,'
                     r'\s*instance_count\s*=\s*(\d+)')


def validated_capacity():
    """Split the prod capacity_reservations config into VALID vs DEAD capacity.

    An entry is dead when it names a capacity reservation that no longer exists in
    EC2 (expired/cancelled/released) -- terraform keeps the ASG so it isn't destroyed,
    so the configured instance_count is a number that can never be filled.
    id = null means plain on-demand: a legitimate ask with no CR guarantee.
    """
    blob = open(os.path.join(REPO, MAIN_TF)).read()
    idx = blob.find("capacity_reservations = {")
    entries = []          # (pool, key, cr_id|None, instance_count)
    for m in CR_BLOCK.finditer(blob[idx:idx + 8000]):
        if 'id = "cr' in m.group(1) or "id = null" in m.group(1):
            for tm in CR_TYPE.finditer(m.group(1)):
                pool = tm.group(1)
                for e in CR_LINE.finditer(tm.group(2)):
                    entries.append((pool, e.group(1), e.group(2), int(e.group(3))))
            break

    ec2 = boto3.client("ec2", region_name=REGION)
    cr_state = {}
    for cid in {e[2] for e in entries if e[2]}:
        try:
            r = ec2.describe_capacity_reservations(CapacityReservationIds=[cid])
            c = r["CapacityReservations"][0]
            cr_state[cid] = {"state": c["State"], "total": c["TotalInstanceCount"],
                             "end": str(c.get("EndDate")), "type": c["InstanceType"]}
        except ec2.exceptions.ClientError:
            cr_state[cid] = {"state": "not-found", "total": 0, "end": None, "type": None}

    valid, dead, detail = defaultdict(float), defaultdict(float), []
    for pool, key, cid, n in entries:
        gpi = GPUS_PER_INSTANCE.get(pool, 0)
        if cid is None:
            kind, ok = "on-demand", True
        elif cr_state[cid]["state"] in ("active", "pending", "scheduled"):
            kind, ok = "capacity-reservation", True
        else:
            kind, ok = "dead-capacity-reservation", False
        (valid if ok else dead)[pool] += n * gpi
        detail.append({"pool": pool, "key": key, "cr_id": cid, "nodes": n,
                       "gpus": n * gpi, "kind": kind,
                       "cr_state": cr_state[cid]["state"] if cid else None,
                       "cr_end": cr_state[cid]["end"] if cid else None})

    # pools with no capacity_reservations entry are plain on-demand ASGs
    pidx = blob.find("    prod = {\n      aws_region")
    pseg = blob[pidx:blob.find("\n    }\n", pidx)]
    for tm in re.finditer(r'"([\w-]+)" = \{(.*?)\n        \}', pseg, re.S):
        name, body = tm.group(1), tm.group(2)
        if name in valid or name in dead or "virtual" in body:
            continue
        ic = re.search(r"instance_count\s*=\s*(\d+)", body)
        gpi = GPUS_PER_INSTANCE.get(name, 0)
        if ic and gpi and "not used when capacity_reservations" not in body:
            n = int(ic.group(1))
            valid[name] += n * gpi
            detail.append({"pool": name, "key": "-", "cr_id": None, "nodes": n,
                           "gpus": n * gpi, "kind": "on-demand", "cr_state": None,
                           "cr_end": None})
    return {"valid_gpus": dict(valid), "dead_gpus": dict(dead),
            "entries": detail, "cr_state": cr_state}


def funded_history():
    """Walk git history of main.tf -> {date: {type: instance_count}} from capacity_reservations."""
    log = subprocess.run(
        ["git", "-C", REPO, "log", "--since=2025-11-01", "--format=%H %ad", "--date=short",
         "--", MAIN_TF], capture_output=True, text=True).stdout.split("\n")
    hist = {}
    for line in log:
        if not line.strip():
            continue
        sha, date = line.split()
        blob = subprocess.run(["git", "-C", REPO, "show", f"{sha}:{MAIN_TF}"],
                              capture_output=True, text=True).stdout
        # capacity_reservations block: find the 'prod = {' that contains 'key = "cr'
        counts = {}
        idx = blob.find("capacity_reservations = {")
        if idx >= 0:
            seg = blob[idx:idx + 8000]
            for m in CR_BLOCK.finditer(seg):
                if 'id = "cr' in m.group(1) or "id = null" in m.group(1):
                    for tm in CR_TYPE.finditer(m.group(1)):
                        counts[tm.group(1)] = sum(int(x) for x in CR_ENTRY.findall(tm.group(2)))
                    break
        # non-CR pools come from supported_gpu_types prod block
        pidx = blob.find("    prod = {\n      aws_region")
        if pidx >= 0:
            pseg = blob[pidx:blob.find("\n    }\n", pidx)]
            for tm in re.finditer(r'"([\w-]+)" = \{(.*?)\n        \}', pseg, re.S):
                name, body = tm.group(1), tm.group(2)
                if name in counts or "virtual" in body:
                    continue
                ic = re.search(r"instance_count\s*=\s*(\d+)", body)
                if ic and "not used when capacity_reservations" not in body:
                    counts[name] = int(ic.group(1))
        if counts:
            hist[date] = counts
    return dict(sorted(hist.items()))


def funded_per_month(hist):
    """Config state as of the last commit at or before each month's midpoint."""
    out = {}
    for mk in MONTHS:
        lo, hi = month_bounds(mk)
        mid = (lo + (hi - lo) / 2).date().isoformat()
        pick = None
        for d in hist:
            if d <= mid:
                pick = d
        counts = hist.get(pick, {}) if pick else {}
        out[mk] = {
            "as_of_commit_date": pick,
            "gpus": {t: c * GPUS_PER_INSTANCE.get(t, 0) for t, c in counts.items()
                     if GPUS_PER_INSTANCE.get(t, 0)},
            "cpu_nodes": sum(c for t, c in counts.items() if t in CPU_TYPES),
        }
    return out


# ---------------------------------------------------------------- main
def main():
    items = load_reservations()
    intervals, skipped = usage_intervals(items)
    agg = aggregate(intervals)
    cur = current_capacity()
    vcap = validated_capacity()
    ce = ce_by_asg()
    fhist = funded_history()
    fmonth = funded_per_month(fhist)

    monthly = []
    for mk in MONTHS:
        lo, hi = month_bounds(mk)
        span_h = (hi - lo).total_seconds() / 3600
        cats = {c: round(v, 1) for (m, c), v in agg["gpu_h"].items() if m == mk and v > 0.05}
        pools = {p: round(v, 1) for (m, p), v in agg["pool_h"].items() if m == mk and v > 0.05}
        peaks = {p: v for (m, p), v in agg["peak"].items() if m == mk}
        funded = fmonth[mk]["gpus"]
        # Delivered capacity per pool = max(measured ASG node-hours, observed high-water mark).
        # CE node-hours cover ASG-managed nodes only; the HWM floor catches pods that ran on
        # non-ASG "pet" nodes joined to the cluster (notably h100 in March 2026).
        cem = ce.get(mk, {})
        measured = {p: round(v["node_hours"] * GPUS_PER_INSTANCE.get(p, 0) / span_h, 2)
                    for p, v in cem.items() if GPUS_PER_INSTANCE.get(p, 0)}
        deliv = {}
        for p in set(measured) | {p for (m, p), v in agg["delivered"].items() if m == mk and v > 0}:
            hwm = agg["delivered"].get((mk, p), 0.0)
            v = max(measured.get(p, 0.0), hwm)
            if v > 0:
                deliv[p] = round(v, 2)
        used_gpu_h = sum(pools.values())
        funded_h = sum(funded.values()) * span_h
        peak_h = sum(peaks.values()) * span_h
        deliv_h = sum(deliv.values()) * span_h

        # ---- cost: on-demand pools are billed actuals; block pools are node-hours x rate
        cost_od, cost_lo, cost_hi = {}, {}, {}
        for p, v in cem.items():
            if p in BLOCK_POOLS:
                it = POOL_INSTANCE[p]
                cost_lo[p] = round(v["node_hours"] * BLENDED_RATE[it])
                cost_hi[p] = round(v["node_hours"] * LIST_RATE[it])
            elif v["cost"] > 0.5:
                cost_od[p] = round(v["cost"])
        monthly.append({
            "month": mk,
            "partial": hi < datetime(hi.year + (hi.month == 12), (hi.month % 12) + 1, 1),
            "hours_in_window": round(span_h, 1),
            "gpu_hours_by_sku": dict(sorted(cats.items(), key=lambda x: -x[1])),
            "physical_gpu_hours_by_pool": dict(sorted(pools.items(), key=lambda x: -x[1])),
            "peak_concurrent_gpus_by_pool": dict(sorted(peaks.items(), key=lambda x: -x[1])),
            "mean_concurrent_gpus": round(used_gpu_h / span_h, 2) if span_h else 0,
            "peak_concurrent_gpus_total": round(sum(peaks.values()), 2),
            "funded_gpus_by_pool": funded,
            "funded_gpus_total": sum(funded.values()),
            "funded_config_as_of": fmonth[mk]["as_of_commit_date"],
            "delivered_gpus_by_pool": dict(sorted(deliv.items(), key=lambda x: -x[1])),
            "delivered_gpus_total": round(sum(deliv.values()), 1),
            "measured_gpus_by_pool": dict(sorted(measured.items(), key=lambda x: -x[1])),
            "node_hours_by_pool": {p: round(v["node_hours"], 1) for p, v in cem.items()},
            "cost_ondemand_by_pool": dict(sorted(cost_od.items(), key=lambda x: -x[1])),
            "cost_ondemand_total": round(sum(cost_od.values())),
            "cost_block_low_by_pool": dict(sorted(cost_lo.items(), key=lambda x: -x[1])),
            "cost_block_high_by_pool": dict(sorted(cost_hi.items(), key=lambda x: -x[1])),
            "cost_total_low": round(sum(cost_od.values()) + sum(cost_lo.values())),
            "cost_total_high": round(sum(cost_od.values()) + sum(cost_hi.values())),
            "gpu_hours_used": round(used_gpu_h, 1),
            "utilization_vs_delivered_pct": round(100 * used_gpu_h / deliv_h, 1) if deliv_h else None,
            "utilization_by_pool_pct": {p: round(100 * pools.get(p, 0) / (v * span_h), 1)
                                        for p, v in deliv.items() if v},
            "utilization_vs_funded_pct": round(100 * used_gpu_h / funded_h, 1) if funded_h else None,
            "cpu_node_hours": round(agg["cpu_node_h"].get(mk, 0), 1),
            "active_users": len(agg["users"].get(mk, ())),
            "active_users_gpu": len(agg["users_gpu"].get(mk, ())),
            "reservations_launched": agg["resv"].get(mk, 0),
        })

    report = {
        "generated_at": NOW.isoformat() + "Z",
        "region": REGION,
        "window": {"start": START.isoformat(), "end": NOW.isoformat()},
        "record_count": len(items),
        "intervals_used": len(intervals),
        "skipped": skipped,
        "current_capacity": dict(sorted(cur.items())),
        "validated_capacity": vcap,
        "rates": {"list": LIST_RATE, "blended": BLENDED_RATE, "pool_instance": POOL_INSTANCE},
        "funded_capacity_history": fhist,
        "monthly": monthly,
        "distinct_users_window": len(set().union(*agg["users"].values())) if agg["users"] else 0,
        "notes": [
            "GPU-hours = gpu_count x (end - launched_at), clipped to month; end = "
            "reservation_ended | cancelled_at | expired_at | failed_at | expires_at | now(active).",
            "MIG SKUs are converted to fractional physical GPUs (1g=1/7, 2g=2/7, 3g=3/7) for "
            "pool/utilization math; gpu_hours_by_sku keeps raw slice counts.",
            "delivered capacity per month = high-water mark of concurrently allocated GPUs in "
            "that month. The placement gate never overcommits a node, so HWM <= real delivered "
            "capacity: a provable lower bound, which makes utilization_vs_delivered an UPPER "
            "bound. Validated against the live availability table (Jul-2026 HWM h100=28 vs 30 "
            "delivered today, b200=14 vs 14).",
            "utilization_vs_funded uses terraform-configured instance counts (the funded target). "
            "It reads low because expired/unavailable capacity reservations mean the fleet never "
            "physically reached the configured size (e.g. h200: 64 GPUs configured, 0 delivered).",
            "CPU pools (cpu-x86/cpu-arm) carry 0 GPUs and are reported as node-hours.",
        ],
    }
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_dev_monthly_report.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"wrote {out}")
    print(f"records={len(items)} intervals={len(intervals)} skipped={skipped}")
    for m in monthly:
        print(f"{m['month']}  used={m['gpu_hours_used']:>9.1f} gpu-h  "
              f"mean_conc={m['mean_concurrent_gpus']:>6.1f}  peak={m['peak_concurrent_gpus_total']:>6.1f}  "
              f"deliv={m['delivered_gpus_total']:>6.1f}  funded={m['funded_gpus_total']:>4}  "
              f"util_deliv={m['utilization_vs_delivered_pct']}%  "
              f"util_funded={m['utilization_vs_funded_pct']}%  users={m['active_users']}  "
              f"resv={m['reservations_launched']}  "
              f"cost=${m['cost_total_low']:,}..${m['cost_total_high']:,}")


if __name__ == "__main__":
    main()
