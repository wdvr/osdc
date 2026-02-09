#!/usr/bin/env python3
"""
SSH ProxyCommand helper for tunneling SSH through WebSocket
Used by ssh with: ssh -o ProxyCommand='gpu-dev-ssh-proxy %h %p' user@host
"""

import sys
import asyncio
import websockets


async def tunnel_ssh(target_host: str, target_port: int):
    """
    Create WebSocket tunnel to SSH server via proxy

    Args:
        target_host: Target SSH hostname
        target_port: Target SSH port
    """
    # Determine proxy URL based on target host
    # Use endswith() for security - prevents hostname spoofing like "fake.devservers.io.attacker.com"
    if target_host.endswith(".test.devservers.io"):
        proxy_host = "ssh.test.devservers.io"
    elif target_host.endswith(".devservers.io"):
        proxy_host = "ssh.devservers.io"
    else:
        print(f"Error: Unsupported domain: {target_host}", file=sys.stderr)
        sys.exit(1)

    # WebSocket URL - wss:// for secure WebSocket
    ws_url = f"wss://{proxy_host}/tunnel/{target_host}"

    try:
        # Connect to WebSocket proxy
        async with websockets.connect(ws_url) as websocket:
            # Set up stdin/stdout for SSH
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)

            await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

            # Create writer for stdout
            writer_transport, writer_protocol = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin, sys.stdout.buffer
            )
            writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)

            async def stdin_to_ws():
                """Forward stdin to WebSocket"""
                try:
                    while True:
                        data = await reader.read(8192)
                        if not data:
                            break
                        await websocket.send(data)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"Error in stdin_to_ws: {e}", file=sys.stderr)

            async def ws_to_stdout():
                """Forward WebSocket to stdout"""
                try:
                    async for message in websocket:
                        if isinstance(message, bytes):
                            writer.write(message)
                            await writer.drain()
                except asyncio.CancelledError:
                    raise
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    print(f"Error in ws_to_stdout: {e}", file=sys.stderr)

            # Run both directions concurrently; when one finishes, cancel the other
            tasks = [
                asyncio.create_task(stdin_to_ws()),
                asyncio.create_task(ws_to_stdout()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            # Propagate exceptions from completed tasks
            for task in done:
                task.result()

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"Error connecting to proxy: HTTP {e.status_code}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error connecting to proxy: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """
    Main entry point for ssh ProxyCommand
    Usage: gpu-dev-ssh-proxy <target_host> <target_port>
    """
    if len(sys.argv) != 3:
        print("Usage: gpu-dev-ssh-proxy <target_host> <target_port>", file=sys.stderr)
        print("This command is meant to be used as SSH ProxyCommand", file=sys.stderr)
        sys.exit(1)

    target_host = sys.argv[1]
    target_port = int(sys.argv[2])

    # Run the async tunnel
    try:
        asyncio.run(tunnel_ssh(target_host, target_port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
