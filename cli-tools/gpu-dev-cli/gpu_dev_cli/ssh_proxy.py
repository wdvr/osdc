#!/usr/bin/env python3
"""
SSH ProxyCommand helper that tunnels SSH through Kubernetes port-forward.

Used by ssh with: ssh -o ProxyCommand='gpu-dev-ssh-proxy %h %p' user@host

The pod name is extracted from the target hostname (%h). This bridges the
local SSH client's stdin/stdout to the pod's sshd (port 22) via the K8s API,
using boto3 for EKS authentication. No kubectl or aws CLI needed.
"""

import sys

from .config import Config
from .kubeconfig import get_k8s_api_client, kube_port_forward_stdio


def main():
    """Main entry point for SSH ProxyCommand.

    Usage: gpu-dev-ssh-proxy <pod_name> <target_port>

    The pod_name is the K8s pod name (e.g., gpu-dev-34f5f9e0).
    target_port is passed by SSH but ignored (we always connect to port 22).
    """
    if len(sys.argv) < 2:
        print("Usage: gpu-dev-ssh-proxy <pod_name> [target_port]", file=sys.stderr)
        print("This command is meant to be used as SSH ProxyCommand", file=sys.stderr)
        sys.exit(1)

    pod_name = sys.argv[1]
    # target_port from SSH is ignored; we always forward to port 22 on the pod

    try:
        config = Config()
        api_client = get_k8s_api_client(config)
        kube_port_forward_stdio(api_client, pod_name, port=22)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
