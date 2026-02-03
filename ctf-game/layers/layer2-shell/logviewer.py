#!/usr/bin/env python3
"""
logviewer - Application Log Viewer
This tool runs with elevated privileges (simulated setuid).
Vulnerability: --path flag allows reading arbitrary files.
"""

import sys
import argparse


def main():
    parser = argparse.ArgumentParser(description="Application Log Viewer v2.1")
    parser.add_argument("--path", default="/var/log/app.log", help="Log file path")
    parser.add_argument("--tail", type=int, help="Show last N lines")
    args = parser.parse_args()

    try:
        with open(args.path) as f:
            content = f.read()

        if args.tail:
            lines = content.split("\n")
            content = "\n".join(lines[-args.tail:])

        print(f"=== {args.path} ===")
        print(content)

    except FileNotFoundError:
        print(f"Error: {args.path} not found", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"Error: Permission denied for {args.path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
