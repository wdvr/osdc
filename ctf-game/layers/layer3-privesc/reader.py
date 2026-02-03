#!/usr/bin/env python3
"""
reader - File reading utility (SUID simulation)
In a real CTF this would be a compiled SUID binary.
Here we simulate the behavior for the challenge.
"""

import sys
import os


def main():
    if len(sys.argv) < 2:
        print("Usage: reader <filename>")
        print("A simple file reader utility")
        sys.exit(1)

    filename = sys.argv[1]

    # In the container, this runs as root due to permissions setup
    try:
        with open(filename) as f:
            print(f.read())
    except FileNotFoundError:
        print(f"Error: {filename} not found", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"Error: Permission denied", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
