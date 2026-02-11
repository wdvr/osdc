"""Kubernetes client setup and exec helpers using Python kubernetes client + boto3.

Eliminates the need for kubectl or aws CLI on the user's PATH.
EKS authentication is handled entirely via boto3 STS presigned URLs.
"""

import base64
import json
import os
import select
import signal
import sys
import tempfile
import termios
import threading
import tty
from pathlib import Path
from typing import Optional, Tuple

import yaml

import boto3
import urllib3
from botocore.signers import RequestSigner
from kubernetes import client as k8s_client
from kubernetes.stream import portforward, stream

# Suppress InsecureRequestWarning when using custom CA
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NAMESPACE = "gpu-dev"
KUBECONFIG_DIR = Path.home() / ".gpu-dev"


def get_eks_token(cluster_name: str, region: str, session: Optional[boto3.Session] = None) -> str:
    """Generate an EKS bearer token using boto3 STS presigned URL.

    This is equivalent to `aws eks get-token` but without needing the AWS CLI.
    """
    if session is None:
        session = boto3.Session()

    sts = session.client("sts", region_name=region)
    service_id = sts.meta.service_model.service_id

    signer = RequestSigner(service_id, region, "sts", "v4", session.get_credentials(), session.events)

    params = {
        "method": "GET",
        "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {},
        "headers": {"x-k8s-aws-id": cluster_name},
        "context": {},
    }

    signed_url = signer.generate_presigned_url(
        params, region_name=region, expires_in=60, operation_name=""
    )

    return "k8s-aws-v1." + base64.urlsafe_b64encode(signed_url.encode()).rstrip(b"=").decode()


def get_eks_cluster_info(cluster_name: str, region: str, session: Optional[boto3.Session] = None) -> dict:
    """Fetch EKS cluster endpoint and CA data via boto3.

    Falls back to reading from ~/.kube/config if eks:DescribeCluster is denied.
    """
    if session is None:
        session = boto3.Session()

    try:
        eks = session.client("eks", region_name=region)
        resp = eks.describe_cluster(name=cluster_name)
        cluster = resp["cluster"]
        return {
            "endpoint": cluster["endpoint"],
            "ca_data": cluster["certificateAuthority"]["data"],
            "name": cluster["name"],
        }
    except Exception:
        # Fall back to reading from local kubeconfig (set up by switch-to.sh)
        return _get_cluster_info_from_kubeconfig(cluster_name, region)


def _get_cluster_info_from_kubeconfig(cluster_name: str, region: Optional[str] = None) -> dict:
    """Extract cluster endpoint and CA from ~/.kube/config.

    When region is provided, matches on both cluster name and region to
    disambiguate clusters with the same name in different regions.
    """
    kubeconfig_path = Path.home() / ".kube" / "config"
    if not kubeconfig_path.exists():
        raise RuntimeError(
            f"~/.kube/config not found and eks:DescribeCluster denied. "
            f"Run: aws eks update-kubeconfig --name {cluster_name}"
        )

    with open(kubeconfig_path) as f:
        kc = yaml.safe_load(f)

    for cluster in kc.get("clusters", []):
        name = cluster.get("name", "")
        # Match cluster name AND region if provided (ARN format: arn:aws:eks:<region>:...)
        if cluster_name not in name:
            continue
        if region and region not in name:
            continue
        data = cluster.get("cluster", {})
        endpoint = data.get("server")
        ca_data = data.get("certificate-authority-data")
        if endpoint and ca_data:
            return {"endpoint": endpoint, "ca_data": ca_data, "name": cluster_name}

    raise RuntimeError(
        f"Cluster {cluster_name} (region={region}) not found in ~/.kube/config. "
        f"Run: aws eks update-kubeconfig --region {region} --name {cluster_name}"
    )


def _write_ca_cert(ca_data: str) -> str:
    """Write base64-decoded CA cert to a temp file, return path."""
    ca_path = KUBECONFIG_DIR / "eks-ca.pem"
    KUBECONFIG_DIR.mkdir(mode=0o700, exist_ok=True)
    ca_bytes = base64.b64decode(ca_data)
    ca_path.write_bytes(ca_bytes)
    ca_path.chmod(0o600)
    return str(ca_path)


def get_k8s_api_client(config) -> k8s_client.ApiClient:
    """Create a configured kubernetes ApiClient for the cluster.

    For local environment: uses standard kubeconfig (k3d context).
    For EKS environments: uses eks:DescribeCluster + boto3 STS token.

    Args:
        config: gpu_dev_cli Config instance (has cluster_name, aws_region, session)
    """
    from kubernetes import config as k8s_config

    env = config.user_config.get("environment", "prod")
    if env == "local":
        k8s_config.load_kube_config(context="k3d-gpu-dev-local")
        return k8s_client.ApiClient()

    cluster_info = get_eks_cluster_info(config.cluster_name, config.aws_region, config.session)
    token = get_eks_token(config.cluster_name, config.aws_region, config.session)
    ca_path = _write_ca_cert(cluster_info["ca_data"])

    configuration = k8s_client.Configuration()
    configuration.host = cluster_info["endpoint"]
    configuration.api_key = {"authorization": f"Bearer {token}"}
    configuration.ssl_ca_cert = ca_path

    return k8s_client.ApiClient(configuration)


def kube_exec_interactive(api_client: k8s_client.ApiClient, pod_name: str, namespace: str = NAMESPACE, shell: str = None) -> int:
    """Interactive exec into a pod - equivalent to kubectl exec -it -- <shell> -l.

    Handles raw terminal mode, bidirectional I/O, and terminal resize.
    Falls back to non-interactive mode when stdin is not a TTY (e.g., piped input).
    Returns the exit code from the remote process.
    """
    if shell is None:
        shell = "/bin/bash"
    is_tty = sys.stdin.isatty()

    v1 = k8s_client.CoreV1Api(api_client)

    rows, cols = _get_terminal_size()

    resp = stream(
        v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=[shell, "-l"],
        stderr=True,
        stdin=True,
        stdout=True,
        tty=is_tty,
        _preload_content=False,
    )

    if not is_tty:
        # Non-interactive: send stdin, collect output, close
        try:
            data = sys.stdin.read()
            if data:
                resp.write_stdin(data)
            # Read all output
            while resp.is_open():
                resp.update(timeout=1)
                if resp.peek_stdout():
                    sys.stdout.write(resp.read_stdout())
                    sys.stdout.flush()
                if resp.peek_stderr():
                    sys.stderr.write(resp.read_stderr())
                    sys.stderr.flush()
        finally:
            try:
                resp.close()
            except Exception:
                pass
        return 0

    # Send initial terminal size
    resp.write_channel(4, json.dumps({"Height": rows, "Width": cols}))

    old_tty = termios.tcgetattr(sys.stdin)

    def handle_resize(signum, frame):
        r, c = _get_terminal_size()
        try:
            resp.write_channel(4, json.dumps({"Height": r, "Width": c}))
        except Exception:
            pass

    old_handler = signal.signal(signal.SIGWINCH, handle_resize)

    try:
        tty.setraw(sys.stdin.fileno())

        # Thread: read from websocket, write to stdout
        stop_event = threading.Event()

        def ws_reader():
            while not stop_event.is_set() and resp.is_open():
                try:
                    resp.update(timeout=0.1)
                except Exception:
                    break
                if resp.peek_stdout():
                    data = resp.read_stdout()
                    sys.stdout.write(data)
                    sys.stdout.flush()
                if resp.peek_stderr():
                    data = resp.read_stderr()
                    sys.stderr.write(data)
                    sys.stderr.flush()

        reader_thread = threading.Thread(target=ws_reader, daemon=True)
        reader_thread.start()

        # Main thread: read from stdin, write to websocket
        while resp.is_open():
            if select.select([sys.stdin], [], [], 0.1)[0]:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    break
                resp.write_stdin(data.decode("utf-8", errors="replace"))

        stop_event.set()
        reader_thread.join(timeout=2)

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
        signal.signal(signal.SIGWINCH, old_handler)
        try:
            resp.close()
        except Exception:
            pass

    return 0


def kube_port_forward_stdio(api_client: k8s_client.ApiClient, pod_name: str, port: int = 22, namespace: str = NAMESPACE):
    """Port-forward to a pod and bridge stdin/stdout.

    Used as SSH ProxyCommand: bridges local SSH client to pod's sshd.
    Binary-safe since port-forward works at the TCP level.
    """
    v1 = k8s_client.CoreV1Api(api_client)

    pf = portforward(
        v1.connect_get_namespaced_pod_portforward,
        pod_name,
        namespace,
        ports=str(port),
    )

    sock = pf.socket(port)
    sock.setblocking(False)

    # Make stdin non-blocking
    stdin_fd = sys.stdin.buffer.fileno()
    stdout_fd = sys.stdout.buffer.fileno()

    try:
        while True:
            readable, _, _ = select.select([stdin_fd, sock], [], [], 1.0)

            if stdin_fd in readable:
                data = os.read(stdin_fd, 8192)
                if not data:
                    break
                sock.sendall(data)

            if sock in readable:
                try:
                    data = sock.recv(8192)
                except (BlockingIOError, OSError):
                    continue
                if not data:
                    break
                os.write(stdout_fd, data)

    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass


def get_connect_command(pod_name: str) -> str:
    """Return the display string for connecting via gpu-dev connect."""
    return f"gpu-dev connect {pod_name}"


def get_kubectl_exec_command(pod_name: str) -> str:
    """Return the display string for kubectl exec (for users who prefer it)."""
    return f"kubectl exec -it {pod_name} -n {NAMESPACE} -- /bin/bash -l"


def _get_terminal_size() -> Tuple[int, int]:
    try:
        cols, rows = os.get_terminal_size()
        return rows, cols
    except OSError:
        return 24, 80
