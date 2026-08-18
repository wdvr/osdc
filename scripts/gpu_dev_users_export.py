#!/usr/bin/env python3
"""Render gpu_dev_users.json into shareable markdown / CSV / plain HTML.

Usage: python3 scripts/gpu_dev_users_export.py
Writes gpu_dev_users.{md,csv,html} next to the JSON.
"""
import csv
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
COLS = [("rank", "#"), ("user", "github_user"), ("meta_user", "meta_user"),
        ("gpu_hours", "GPU-hours"), ("cpu_node_hours", "CPU node-hours"),
        ("wall_hours", "wall-hours"), ("reservations", "reservations"),
        ("last_active", "last active"), ("types", "GPU mix (GPU-hours)")]


def load():
    with open(os.path.join(HERE, "gpu_dev_users.json")) as f:
        d = json.load(f)
    rows = []
    for i, u in enumerate(d["users"], 1):
        rows.append({
            "rank": i, "user": u["user"], "meta_user": u["meta_user"] or "(external)",
            "gpu_hours": u["gpu_hours"], "cpu_node_hours": u["cpu_node_hours"],
            "wall_hours": u["wall_hours"], "reservations": u["reservations"],
            "last_active": u["last_active"],
            "types": ", ".join(f"{k} {v:g}" for k, v in u["top_types"].items()),
        })
    return d, rows


def header(d):
    w = d["window"]
    top10 = sum(r["gpu_hours"] for r in d["users"][:10])
    return (f"gpu-dev usage by user - last {w['days']} days "
            f"({w['start'][:10]} to {w['end'][:10]} UTC)",
            [f"{d['distinct_users']} distinct users, "
             f"{d['total_gpu_hours']:,.0f} GPU-hours, "
             f"{d['total_cpu_node_hours']:,.0f} CPU node-hours, "
             f"{sum(u['reservations'] for u in d['users']):,} reservations.",
             f"Top 10 users account for {100 * top10 / d['total_gpu_hours']:.0f}% "
             f"of all GPU-hours.",
             "GPU-hours = gpu_count x hours-live, credited only for the part of each "
             "reservation inside the window. CPU pods carry 0 GPUs and are counted as "
             "node-hours. MIG slices count as 1 whole GPU (single-digit hours either way).",
             "Source: DynamoDB pytorch-gpu-dev-reservations in us-east-2 (prod, 99.6% of "
             "activity), us-east-1 (spot) and us-west-1 (staging). meta_user is the SSO "
             "role-session identity the reservation was authenticated with.",
             f"Generated {d['generated_at'][:19]}Z by scripts/gpu_dev_users.py."])


def main():
    d, rows = load()
    title, notes = header(d)
    num = {"gpu_hours", "cpu_node_hours", "wall_hours"}

    md = [f"# {title}", ""] + [f"{n}\n" for n in notes]
    md += ["| " + " | ".join(lbl for _, lbl in COLS) + " |",
           "|" + "|".join("--:" if k in num | {"rank", "reservations"} else "---"
                          for k, _ in COLS) + "|"]
    for r in rows:
        md.append("| " + " | ".join(
            f"{r[k]:,.1f}" if k in num else str(r[k]) for k, _ in COLS) + " |")
    with open(os.path.join(HERE, "gpu_dev_users.md"), "w") as f:
        f.write("\n".join(md) + "\n")

    with open(os.path.join(HERE, "gpu_dev_users.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([lbl for _, lbl in COLS])
        for r in rows:
            w.writerow([r[k] for k, _ in COLS])

    h = [f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>",
         "<style>body{font:14px -apple-system,system-ui,sans-serif;margin:2rem;"
         "max-width:1100px}table{border-collapse:collapse;width:100%}"
         "th,td{padding:4px 9px;border-bottom:1px solid #e3e3e3;text-align:left;"
         "white-space:nowrap}th{background:#f6f6f6;position:sticky;top:0}"
         "td.n{text-align:right;font-variant-numeric:tabular-nums}"
         "tr:hover td{background:#fbfbf5}p{color:#555;max-width:70ch}"
         "code{background:#f2f2f2;padding:1px 4px}</style>",
         f"<h2>{html.escape(title)}</h2>"]
    h += [f"<p>{html.escape(n)}</p>" for n in notes]
    h.append("<table><tr>" + "".join(f"<th>{html.escape(l)}</th>" for _, l in COLS)
             + "</tr>")
    for r in rows:
        cells = "".join(
            f'<td class=n>{r[k]:,.1f}</td>' if k in num
            else f'<td class={"n" if k in ("rank", "reservations") else "x"}>'
                 f'{html.escape(str(r[k]))}</td>'
            for k, _ in COLS)
        h.append(f"<tr>{cells}</tr>")
    h.append("</table>")
    with open(os.path.join(HERE, "gpu_dev_users.html"), "w") as f:
        f.write("\n".join(h) + "\n")

    print("wrote gpu_dev_users.{md,csv,html} in", HERE)


if __name__ == "__main__":
    main()
