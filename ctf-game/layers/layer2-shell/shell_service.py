#!/usr/bin/env python3
"""
Layer 2: Restricted Shell Service
A TCP service that provides a limited shell environment.
Players must authenticate with token from Layer 1, then explore to find clues.
"""

import os
import socket
import threading
import subprocess
import shlex

ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "layer2-access-token-7f3d9a2b")
HOST = "0.0.0.0"
PORT = 9000

# Allowed commands in restricted shell
ALLOWED_COMMANDS = {
    "help": "Show available commands",
    "ls": "List directory contents",
    "cat": "Read file contents (restricted paths)",
    "pwd": "Print working directory",
    "cd": "Change directory",
    "whoami": "Show current user",
    "id": "Show user/group IDs",
    "find": "Find files (limited)",
    "env": "Show environment variables",
    "echo": "Echo text",
    "logviewer": "View application logs (special tool)",
    "exit": "Exit the shell",
    "hint": "Get a hint",
}

# Paths that can be read
READABLE_PATHS = [
    "/home/ctfuser",
    "/var/log",
    "/tmp",
    "/app",
    "/etc/passwd",
    "/etc/hosts",
]

# Blocked paths
BLOCKED_PATHS = [
    "/opt/secrets",
    "/etc/shadow",
    "/etc/corp",
    "/flags",
]


def send_motd(conn):
    """Send message of the day"""
    try:
        with open("/app/motd.txt") as f:
            motd = f.read()
    except:
        motd = "Welcome to the restricted shell.\n"
    conn.sendall(motd.encode())


def is_path_allowed(path, for_read=True):
    """Check if path access is allowed"""
    abs_path = os.path.abspath(path)

    # Check blocked paths first
    for blocked in BLOCKED_PATHS:
        if abs_path.startswith(blocked):
            return False

    # For reading, check allowed paths
    if for_read:
        for allowed in READABLE_PATHS:
            if abs_path.startswith(allowed) or abs_path == allowed:
                return True
        # Also allow reading current directory
        return True

    return True


def execute_command(cmd_line, cwd):
    """Execute a command in the restricted shell"""
    parts = shlex.split(cmd_line)
    if not parts:
        return "", cwd

    cmd = parts[0]
    args = parts[1:] if len(parts) > 1 else []

    if cmd not in ALLOWED_COMMANDS and cmd not in ["cat", "ls", "cd", "find", "logviewer"]:
        return f"Command not allowed: {cmd}\nType 'help' for available commands.\n", cwd

    try:
        if cmd == "help":
            result = "Available commands:\n"
            for c, desc in ALLOWED_COMMANDS.items():
                result += f"  {c:12} - {desc}\n"
            result += "\nHint: Explore the filesystem. Some files may have interesting info.\n"
            return result, cwd

        elif cmd == "hint":
            return """Hints:
1. Check /home/ctfuser for notes left by previous users
2. The 'logviewer' tool might have special privileges
3. Configuration files sometimes contain secrets
4. Try: logviewer --path /etc/corp/services.conf
""", cwd

        elif cmd == "pwd":
            return cwd + "\n", cwd

        elif cmd == "whoami":
            return "ctfuser\n", cwd

        elif cmd == "id":
            return "uid=1000(ctfuser) gid=1000(ctfuser) groups=1000(ctfuser),4(adm)\n", cwd

        elif cmd == "env":
            # Show some env vars but not the token
            result = "PATH=/usr/local/bin:/usr/bin:/bin\n"
            result += "HOME=/home/ctfuser\n"
            result += "USER=ctfuser\n"
            result += "SHELL=/bin/restricted\n"
            result += "LAYER3_HOST=layer3-priv\n"
            result += "LAYER3_PORT=8888\n"
            return result, cwd

        elif cmd == "cd":
            if not args:
                new_cwd = "/home/ctfuser"
            else:
                target = args[0]
                if target.startswith("/"):
                    new_cwd = target
                else:
                    new_cwd = os.path.normpath(os.path.join(cwd, target))

            if os.path.isdir(new_cwd):
                return "", new_cwd
            else:
                return f"cd: {new_cwd}: No such directory\n", cwd

        elif cmd == "ls":
            target = args[0] if args else cwd
            if not target.startswith("/"):
                target = os.path.normpath(os.path.join(cwd, target))

            if not os.path.exists(target):
                return f"ls: {target}: No such file or directory\n", cwd

            if os.path.isfile(target):
                return f"{os.path.basename(target)}\n", cwd

            try:
                files = os.listdir(target)
                result = ""
                for f in sorted(files):
                    full_path = os.path.join(target, f)
                    if os.path.isdir(full_path):
                        result += f"{f}/\n"
                    else:
                        result += f"{f}\n"
                return result if result else "(empty directory)\n", cwd
            except PermissionError:
                return f"ls: {target}: Permission denied\n", cwd

        elif cmd == "cat":
            if not args:
                return "cat: missing file operand\n", cwd

            target = args[0]
            if not target.startswith("/"):
                target = os.path.normpath(os.path.join(cwd, target))

            # Check if blocked
            for blocked in BLOCKED_PATHS:
                if target.startswith(blocked):
                    return f"cat: {target}: Permission denied\n", cwd

            try:
                with open(target) as f:
                    return f.read(), cwd
            except FileNotFoundError:
                return f"cat: {target}: No such file\n", cwd
            except PermissionError:
                return f"cat: {target}: Permission denied\n", cwd
            except IsADirectoryError:
                return f"cat: {target}: Is a directory\n", cwd

        elif cmd == "find":
            # Limited find
            if not args:
                args = ["."]
            start = args[0] if not args[0].startswith("-") else "."
            if not start.startswith("/"):
                start = os.path.normpath(os.path.join(cwd, start))

            result = ""
            try:
                for root, dirs, files in os.walk(start):
                    # Skip blocked paths
                    skip = False
                    for blocked in BLOCKED_PATHS:
                        if root.startswith(blocked):
                            skip = True
                            break
                    if skip:
                        continue

                    for f in files[:20]:  # Limit output
                        result += os.path.join(root, f) + "\n"
                    if len(result.split("\n")) > 50:
                        result += "... (output truncated)\n"
                        break
            except PermissionError:
                pass

            return result if result else "No files found.\n", cwd

        elif cmd == "logviewer":
            # This is the "privileged" tool that can read restricted files
            # It's intentionally vulnerable - allows arbitrary file read via --path

            if "--help" in args or "-h" in args:
                return """logviewer - Application Log Viewer (v2.1)
Usage: logviewer [OPTIONS]

Options:
  --path FILE    Read specific log file (default: /var/log/app.log)
  --tail N       Show last N lines
  --help         Show this help

Note: This tool runs with elevated privileges for log access.
""", cwd

            # Parse --path argument
            target = "/var/log/app.log"
            for i, arg in enumerate(args):
                if arg == "--path" and i + 1 < len(args):
                    target = args[i + 1]

            # THE VULNERABILITY: logviewer can read ANY file (simulating setuid)
            try:
                with open(target) as f:
                    content = f.read()
                return f"=== {target} ===\n{content}\n", cwd
            except FileNotFoundError:
                return f"logviewer: {target}: No such file\n", cwd
            except Exception as e:
                return f"logviewer: Error reading {target}\n", cwd

        elif cmd == "echo":
            return " ".join(args) + "\n", cwd

        elif cmd == "exit":
            return "QUIT", cwd

        else:
            return f"Command not found: {cmd}\n", cwd

    except Exception as e:
        return f"Error: {str(e)}\n", cwd


def handle_client(conn, addr):
    """Handle a single client connection"""
    print(f"Connection from {addr}")

    try:
        # Authentication
        conn.sendall(b"=== Restricted Shell Service ===\n")
        conn.sendall(b"Enter access token: ")

        token = conn.recv(1024).decode().strip()

        if token != ACCESS_TOKEN:
            conn.sendall(b"Access denied. Invalid token.\n")
            conn.sendall(b"Hint: Check the web service for leaked credentials.\n")
            conn.close()
            return

        conn.sendall(b"Access granted.\n\n")
        send_motd(conn)

        cwd = "/home/ctfuser"

        while True:
            prompt = f"ctfuser@layer2:{cwd}$ "
            conn.sendall(prompt.encode())

            try:
                cmd = conn.recv(1024).decode().strip()
            except:
                break

            if not cmd:
                continue

            result, cwd = execute_command(cmd, cwd)

            if result == "QUIT":
                conn.sendall(b"Goodbye!\n")
                break

            if result:
                conn.sendall(result.encode())

    except Exception as e:
        print(f"Error with client {addr}: {e}")
    finally:
        conn.close()
        print(f"Connection closed from {addr}")


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(10)

    print(f"Restricted Shell Service listening on {HOST}:{PORT}")

    while True:
        conn, addr = server.accept()
        thread = threading.Thread(target=handle_client, args=(conn, addr))
        thread.daemon = True
        thread.start()


if __name__ == "__main__":
    main()
