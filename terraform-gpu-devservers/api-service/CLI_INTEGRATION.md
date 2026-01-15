# CLI Integration Guide

## Overview

The API now supports **AWS-based authentication with token exchange**. Users authenticate once with their AWS credentials (`SSOCloudDevGpuReservation` role) and receive a time-limited API key.

## Authentication Flow

```
┌─────────┐                    ┌─────────┐                 ┌─────────┐
│   CLI   │                    │   API   │                 │   AWS   │
└────┬────┘                    └────┬────┘                 └────┬────┘
     │                              │                           │
     │ 1. gpu-dev login             │                           │
     │ (gets AWS credentials)       │                           │
     │                              │                           │
     │ 2. POST /v1/auth/aws-login   │                           │
     │    {aws_access_key, ...}     │                           │
     ├─────────────────────────────>│                           │
     │                              │                           │
     │                              │ 3. Verify credentials     │
     │                              │    STS GetCallerIdentity  │
     │                              ├──────────────────────────>│
     │                              │                           │
     │                              │ 4. Identity + ARN         │
     │                              │<──────────────────────────┤
     │                              │                           │
     │                              │ 5. Check role             │
     │                              │    (SSOCloudDevGpu...)    │
     │                              │                           │
     │ 6. API key (expires in 30d)  │                           │
     │<─────────────────────────────┤                           │
     │                              │                           │
     │ 7. Save API key locally      │                           │
     │    ~/.gpu-dev/credentials    │                           │
     │                              │                           │
     │ 8. All future requests       │                           │
     │    Authorization: Bearer ... │                           │
     ├─────────────────────────────>│                           │
     │                              │                           │
     │ 9. (after 30 days)           │                           │
     │    API returns 403 Expired   │                           │
     │<─────────────────────────────┤                           │
     │                              │                           │
     │ 10. Auto re-authenticate     │                           │
     │     (repeat from step 2)     │                           │
     │                              │                           │
```

## CLI Implementation

### 1. Add AWS Login Function

Create `cli-tools/gpu-dev-cli/gpu_dev_cli/aws_auth.py`:

```python
import json
import os
from pathlib import Path
import boto3
import requests
from botocore.exceptions import ClientError, NoCredentialsError


class AWSAuth:
    """Handle AWS-based authentication for GPU Dev API"""

    def __init__(self, api_url: str):
        self.api_url = api_url
        self.credentials_file = Path.home() / ".gpu-dev" / "credentials.json"

    def get_aws_credentials(self):
        """Get AWS credentials from current session"""
        try:
            session = boto3.Session()
            credentials = session.get_credentials()

            if credentials is None:
                raise NoCredentialsError()

            # Get current credentials (handles assumed roles, SSO, etc.)
            creds = credentials.get_frozen_credentials()

            return {
                'aws_access_key_id': creds.access_key,
                'aws_secret_access_key': creds.secret_key,
                'aws_session_token': creds.token  # May be None for IAM users
            }
        except NoCredentialsError:
            raise Exception(
                "No AWS credentials found. Please configure AWS credentials:\n"
                "  - Run 'aws configure' for long-term credentials\n"
                "  - Run 'aws sso login' for SSO\n"
                "  - Or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env vars"
            )

    def login(self):
        """Authenticate with AWS credentials and get API key"""
        print("🔐 Authenticating with AWS...")

        # Get AWS credentials
        try:
            creds = self.get_aws_credentials()
        except Exception as e:
            print(f"❌ Failed to get AWS credentials: {e}")
            return False

        # Exchange for API key
        try:
            response = requests.post(
                f"{self.api_url}/v1/auth/aws-login",
                json={
                    'aws_access_key_id': creds['aws_access_key_id'],
                    'aws_secret_access_key': creds['aws_secret_access_key'],
                    'aws_session_token': creds.get('aws_session_token')
                },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            # Save credentials
            self.save_credentials(data)

            print(f"✅ Authenticated successfully!")
            print(f"   Username: {data['username']}")
            print(f"   AWS ARN: {data['aws_arn']}")
            print(f"   Expires: {data['expires_at']}")
            print(f"   API key saved to: {self.credentials_file}")

            return True

        except requests.HTTPError as e:
            if e.response.status_code == 403:
                print(f"❌ Access denied: {e.response.json().get('detail')}")
                print("   Required role: SSOCloudDevGpuReservation")
            elif e.response.status_code == 401:
                print(f"❌ Authentication failed: {e.response.json().get('detail')}")
            else:
                print(f"❌ Login failed: {e.response.text}")
            return False
        except Exception as e:
            print(f"❌ Login failed: {e}")
            return False

    def save_credentials(self, data: dict):
        """Save API key and metadata to disk"""
        self.credentials_file.parent.mkdir(exist_ok=True)

        credentials = {
            'api_key': data['api_key'],
            'username': data['username'],
            'expires_at': data['expires_at'],
            'aws_arn': data.get('aws_arn')
        }

        self.credentials_file.write_text(json.dumps(credentials, indent=2))
        self.credentials_file.chmod(0o600)  # Readable only by owner

    def load_credentials(self):
        """Load saved credentials"""
        if not self.credentials_file.exists():
            return None

        try:
            return json.loads(self.credentials_file.read_text())
        except Exception:
            return None

    def get_api_key(self, auto_refresh=True):
        """
        Get valid API key, automatically refreshing if expired

        Args:
            auto_refresh: If True, automatically re-authenticate if key expired

        Returns:
            str: Valid API key
        """
        creds = self.load_credentials()

        if not creds:
            if auto_refresh:
                print("⚠️  No API key found. Logging in...")
                self.login()
                creds = self.load_credentials()
            else:
                raise Exception("No API key found. Run: gpu-dev login")

        # Check expiration
        from datetime import datetime
        expires_at = datetime.fromisoformat(creds['expires_at'].replace('Z', '+00:00'))
        now = datetime.now(expires_at.tzinfo)

        if expires_at < now:
            if auto_refresh:
                print("⚠️  API key expired. Re-authenticating...")
                self.login()
                creds = self.load_credentials()
            else:
                raise Exception("API key expired. Run: gpu-dev login")

        return creds['api_key']

    def is_authenticated(self):
        """Check if user has valid credentials"""
        creds = self.load_credentials()
        if not creds:
            return False

        # Check if expired
        from datetime import datetime
        try:
            expires_at = datetime.fromisoformat(creds['expires_at'].replace('Z', '+00:00'))
            return expires_at > datetime.now(expires_at.tzinfo)
        except Exception:
            return False
```

### 2. Add Login Command to CLI

Update `cli-tools/gpu-dev-cli/gpu_dev_cli/cli.py`:

```python
import click
from .aws_auth import AWSAuth
from .config import get_api_url

@click.group()
def cli():
    """GPU Dev CLI"""
    pass

@cli.command()
def login():
    """
    Authenticate with AWS credentials

    This command uses your current AWS credentials (from aws configure,
    aws sso login, or environment variables) to obtain an API key.

    The API key is saved locally and used for all subsequent commands.
    Keys expire after 30 days and are automatically refreshed.
    """
    api_url = get_api_url()
    auth = AWSAuth(api_url)

    if auth.login():
        click.echo("✅ Login successful! You can now use gpu-dev commands.")
    else:
        click.echo("❌ Login failed. Please check your AWS credentials.")
        exit(1)

@cli.command()
def whoami():
    """Show current authentication status"""
    api_url = get_api_url()
    auth = AWSAuth(api_url)

    if not auth.is_authenticated():
        click.echo("❌ Not authenticated. Run: gpu-dev login")
        exit(1)

    creds = auth.load_credentials()
    click.echo(f"✅ Authenticated as: {creds['username']}")
    click.echo(f"   AWS ARN: {creds.get('aws_arn', 'N/A')}")
    click.echo(f"   Expires: {creds['expires_at']}")
```

### 3. Update Existing Commands to Use Auth

Update `cli-tools/gpu-dev-cli/gpu_dev_cli/reservations.py`:

```python
from .aws_auth import AWSAuth
from .config import get_api_url
import requests

def submit_reservation(image, instance_type, duration_hours, **kwargs):
    """Submit a reservation to the API"""

    # Get API key (auto-refresh if expired)
    api_url = get_api_url()
    auth = AWSAuth(api_url)

    try:
        api_key = auth.get_api_key(auto_refresh=True)
    except Exception as e:
        print(f"❌ Authentication error: {e}")
        print("   Run: gpu-dev login")
        return None

    # Make authenticated request
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }

    payload = {
        'image': image,
        'instance_type': instance_type,
        'duration_hours': duration_hours,
        **kwargs
    }

    try:
        response = requests.post(
            f"{api_url}/v1/jobs/submit",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        return response.json()

    except requests.HTTPError as e:
        if e.response.status_code == 403 and 'expired' in e.response.text.lower():
            # Token expired, force re-auth
            print("⚠️  API key expired, re-authenticating...")
            auth.login()
            # Retry once
            api_key = auth.get_api_key(auto_refresh=False)
            headers['Authorization'] = f'Bearer {api_key}'
            response = requests.post(f"{api_url}/v1/jobs/submit", headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        else:
            raise
```

### 4. Configuration Helper

Create `cli-tools/gpu-dev-cli/gpu_dev_cli/config.py`:

```python
import os

def get_api_url():
    """Get API URL from environment or default"""
    return os.getenv(
        'GPU_DEV_API_URL',
        'https://api.gpudev.example.com'  # Update with actual URL
    )
```

## User Experience

### First Time Setup

```bash
# User already has AWS SSO configured
$ aws sso login

# Authenticate with API (one command)
$ gpu-dev login
🔐 Authenticating with AWS...
✅ Authenticated successfully!
   Username: john
   AWS ARN: arn:aws:sts::123456789:assumed-role/SSOCloudDevGpuReservation/john
   Expires: 2024-02-15T00:00:00Z
   API key saved to: /Users/john/.gpu-dev/credentials.json

# Now all commands work
$ gpu-dev submit --image pytorch/pytorch:latest --instance p5.48xlarge
✅ Job submitted: abc-123-def-456
```

### Daily Usage (Seamless)

```bash
# User doesn't need to think about auth
$ gpu-dev submit --image my-training:v2 --instance p5.48xlarge
✅ Job submitted: xyz-789-abc-123

# Works even if API key expired (auto-refresh)
$ gpu-dev submit --image my-model:latest --instance p5.48xlarge
⚠️  API key expired. Re-authenticating...
🔐 Authenticating with AWS...
✅ Authenticated successfully!
✅ Job submitted: def-456-ghi-789
```

### Check Auth Status

```bash
$ gpu-dev whoami
✅ Authenticated as: john
   AWS ARN: arn:aws:sts::123456789:assumed-role/SSOCloudDevGpuReservation/john
   Expires: 2024-02-15T00:00:00Z
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GPU_DEV_API_URL` | - | API endpoint URL |
| `AWS_PROFILE` | `default` | AWS profile to use |
| `AWS_REGION` | `us-east-1` | AWS region |

## Security Considerations

1. **API Key Storage**: Keys stored in `~/.gpu-dev/credentials.json` with `0600` permissions
2. **No AWS Credentials Stored**: Only temporary API keys stored, not AWS credentials
3. **Automatic Expiration**: Keys expire after 30 days (configurable)
4. **Automatic Refresh**: CLI handles expiration transparently
5. **Role Verification**: API verifies AWS role on every login

## Migration from SQS

Users don't need to change anything! Just run `gpu-dev login` once:

```bash
# Old behavior (SQS)
$ gpu-dev submit ...  # Uses AWS credentials → SQS

# New behavior (API)
$ gpu-dev login       # One-time: Get API key
$ gpu-dev submit ...  # Uses API key → API → PGMQ
```

Same commands, same experience!

