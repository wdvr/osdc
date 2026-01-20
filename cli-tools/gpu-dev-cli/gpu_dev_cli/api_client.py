"""API client for GPU Dev service"""

import json
import os
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional
from rich.console import Console

console = Console()


class APIClient:
    """Client for interacting with GPU Dev API service"""

    # Credentials file path
    CREDENTIALS_FILE = Path.home() / ".gpu-dev" / "credentials"

    def __init__(self, config):
        """
        Initialize API client

        Args:
            config: Config instance with AWS session
        """
        self.config = config
        self.api_url = self._get_api_url()
        self.api_key = None
        self.api_key_expires_at = None

        # Load existing API key if available
        self._load_credentials()

    def _get_api_url(self) -> str:
        """
        Get API URL from environment, config, or defaults

        Priority:
        1. GPU_DEV_API_URL environment variable
        2. api_url in user config
        3. Environment-specific default (test/prod)

        Returns:
            API URL (e.g., https://d1234.cloudfront.net)
        """
        # 1. Check if API URL is set in environment variable
        if api_url := os.getenv("GPU_DEV_API_URL"):
            return api_url.rstrip("/")

        # 2. Check if URL is in user config
        if api_url := self.config.get("api_url"):
            return api_url.rstrip("/")

        # 3. Check environment-specific default
        env_name = self.config.get("environment") or "prod"
        env_config = self.config.ENVIRONMENTS.get(env_name, {})
        if api_url := env_config.get("api_url"):
            return api_url.rstrip("/")

        # No URL configured anywhere
        raise RuntimeError(
            "GPU_DEV_API_URL not configured.\n\n"
            "Set it using one of these methods:\n\n"
            "1. Environment variable:\n"
            "   export GPU_DEV_API_URL=https://your-cloudfront-url\n\n"
            "2. Config command:\n"
            "   gpu-dev config set api_url https://your-cloudfront-url\n\n"
        )

    def _load_credentials(self) -> None:
        """Load API key from credentials if exists and not expired"""
        try:
            if not self.CREDENTIALS_FILE.exists():
                return

            with open(self.CREDENTIALS_FILE, "r") as f:
                creds = json.load(f)

            api_key = creds.get("api_key")
            expires_at_str = creds.get("expires_at")

            if not api_key or not expires_at_str:
                return

            # Parse expiration time
            expires_str = expires_at_str.replace("Z", "+00:00")
            expires_at = datetime.fromisoformat(expires_str)

            # Check if key is still valid (with 5 minute buffer)
            buffer = timedelta(minutes=5)
            if expires_at > datetime.now(timezone.utc) + buffer:
                self.api_key = api_key
                self.api_key_expires_at = expires_at
            else:
                # Key expired, delete file
                self.CREDENTIALS_FILE.unlink(missing_ok=True)

        except Exception as e:
            # If error loading credentials, continue without them
            msg = f"[yellow]Warning: Could not load credentials: {e}[/yellow]"
            console.print(msg)

    def _save_credentials(self, api_key: str, expires_at: str) -> None:
        """Save API key to credentials file"""
        try:
            self.CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)

            creds = {
                "api_key": api_key,
                "expires_at": expires_at,
            }

            with open(self.CREDENTIALS_FILE, "w") as f:
                json.dump(creds, f, indent=2)

            # Set restrictive permissions (owner read/write only)
            os.chmod(self.CREDENTIALS_FILE, 0o600)

        except Exception as e:
            msg = f"[yellow]Warning: Could not save credentials: {e}[/yellow]"
            console.print(msg)

    def _get_aws_credentials(self) -> Dict[str, str]:
        """
        Get AWS credentials from the session

        Returns:
            Dict with aws_access_key_id, aws_secret_access_key,
            and optionally aws_session_token
        """
        try:
            # Get credentials from boto3 session
            credentials = self.config.session.get_credentials()

            if not credentials:
                raise RuntimeError("No AWS credentials found")

            # Get frozen credentials to access values
            frozen_creds = credentials.get_frozen_credentials()

            creds_dict = {
                "aws_access_key_id": frozen_creds.access_key,
                "aws_secret_access_key": frozen_creds.secret_key,
            }

            # Add session token if present (for assumed roles/SSO)
            if frozen_creds.token:
                creds_dict["aws_session_token"] = frozen_creds.token

            return creds_dict

        except Exception as e:
            raise RuntimeError(f"Failed to get AWS credentials: {e}")

    def authenticate(self, force: bool = False) -> bool:
        """
        Authenticate with API service using AWS credentials

        Args:
            force: Force re-authentication even if we have a valid API key

        Returns:
            True if authentication succeeded
        """
        # If we have a valid API key and not forcing re-auth, skip
        if not force and self.api_key and self.api_key_expires_at:
            buffer = timedelta(minutes=5)
            if self.api_key_expires_at > datetime.now(timezone.utc) + buffer:
                return True

        try:
            # Get AWS credentials
            aws_creds = self._get_aws_credentials()

            # Call API login endpoint
            url = f"{self.api_url}/v1/auth/aws-login"
            response = requests.post(url, json=aws_creds, timeout=30)

            if response.status_code != 200:
                if response.text:
                    error_detail = response.json().get(
                        "detail", response.text
                    )
                else:
                    error_detail = "Unknown error"
                raise RuntimeError(f"Authentication failed: {error_detail}")

            data = response.json()

            # Save credentials
            self.api_key = data["api_key"]
            self.api_key_expires_at = datetime.fromisoformat(
                data["expires_at"].replace("Z", "+00:00")
            )

            self._save_credentials(self.api_key, data["expires_at"])

            return True

        except requests.RequestException as e:
            raise RuntimeError(f"Failed to connect to API: {e}")
        except Exception as e:
            raise RuntimeError(f"Authentication error: {e}")

    def _ensure_authenticated(self) -> None:
        """Ensure we have a valid API key, authenticating if necessary"""
        if not self.api_key or not self.api_key_expires_at:
            self.authenticate()
        else:
            buffer = timedelta(minutes=5)
            now_utc = datetime.now(timezone.utc)
            if self.api_key_expires_at <= now_utc + buffer:
                # API key expired or expiring soon, re-authenticate
                self.authenticate()

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make authenticated API request

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "/v1/jobs/submit")
            data: Request body data
            params: Query parameters

        Returns:
            Response data as dict
        """
        self._ensure_authenticated()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        url = f"{self.api_url}{endpoint}"

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                params=params,
                timeout=30
            )

            # Handle 401/403 by trying to re-authenticate once
            if response.status_code in (401, 403):
                self.authenticate(force=True)
                headers["Authorization"] = f"Bearer {self.api_key}"
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                    params=params,
                    timeout=30
                )

            # Raise for other HTTP errors
            response.raise_for_status()

            return response.json()

        except requests.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("detail", str(e))
                except Exception:
                    error_msg = str(e)
            else:
                error_msg = str(e)
            raise RuntimeError(f"API request failed: {error_msg}")

    def submit_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit a GPU job to the queue

        Args:
            job_data: Job parameters

        Returns:
            Response with job_id, status, message
        """
        return self._make_request("POST", "/v1/jobs/submit", data=job_data)

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get job/reservation details

        Args:
            job_id: Job ID (reservation_id)

        Returns:
            Complete job details including status, connection info, etc.
        """
        return self._make_request("GET", f"/v1/jobs/{job_id}")

    def list_jobs(
        self,
        status_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        List user's jobs with filtering

        Args:
            status_filter: Comma-separated statuses to filter by
                          (e.g., "active,pending")
            limit: Maximum number of jobs to return (1-500)
            offset: Number of jobs to skip for pagination

        Returns:
            {
                "jobs": [job_details...],
                "total": total_count,
                "limit": limit,
                "offset": offset
            }
        """
        params = {"limit": limit, "offset": offset}
        if status_filter:
            params["status"] = status_filter

        return self._make_request("GET", "/v1/jobs", params=params)

    def rotate_api_key(self) -> Dict[str, Any]:
        """
        Rotate API key (get a new one)

        Returns:
            New API key information
        """
        response = self._make_request("POST", "/v1/keys/rotate")

        # Save new credentials
        self.api_key = response["api_key"]
        self.api_key_expires_at = datetime.fromisoformat(
            response["expires_at"].replace("Z", "+00:00")
        )
        self._save_credentials(self.api_key, response["expires_at"])

        return response

    def health_check(self) -> Dict[str, Any]:
        """
        Check API health

        Returns:
            Health status
        """
        try:
            response = requests.get(f"{self.api_url}/health", timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise RuntimeError(f"Health check failed: {e}")

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """
        Cancel a job/reservation
        
        Args:
            job_id: Job ID (reservation_id)
            
        Returns:
            Action response with status
        """
        return self._make_request("POST", f"/v1/jobs/{job_id}/cancel")

    def extend_job(self, job_id: str, extension_hours: int) -> Dict[str, Any]:
        """
        Extend job duration
        
        Args:
            job_id: Job ID (reservation_id)
            extension_hours: Number of hours to extend
            
        Returns:
            Action response with status
        """
        data = {"extension_hours": extension_hours}
        return self._make_request("POST", f"/v1/jobs/{job_id}/extend", data=data)

    def enable_jupyter(self, job_id: str) -> Dict[str, Any]:
        """
        Enable Jupyter Lab for a job
        
        Args:
            job_id: Job ID (reservation_id)
            
        Returns:
            Action response with status
        """
        return self._make_request("POST", f"/v1/jobs/{job_id}/jupyter/enable")

    def disable_jupyter(self, job_id: str) -> Dict[str, Any]:
        """
        Disable Jupyter Lab for a job
        
        Args:
            job_id: Job ID (reservation_id)
            
        Returns:
            Action response with status
        """
        return self._make_request("POST", f"/v1/jobs/{job_id}/jupyter/disable")

    def add_user(self, job_id: str, github_username: str) -> Dict[str, Any]:
        """
        Add a user to a job (fetch GitHub SSH keys)
        
        Args:
            job_id: Job ID (reservation_id)
            github_username: GitHub username for SSH key retrieval
            
        Returns:
            Action response with status
        """
        data = {"github_username": github_username}
        return self._make_request("POST", f"/v1/jobs/{job_id}/users", data=data)

    def get_gpu_availability(self) -> Dict[str, Any]:
        """
        Get current GPU availability for all GPU types
        
        Returns:
            {
                "availability": {
                    "h100": {
                        "gpu_type": "h100",
                        "total": 16,
                        "available": 8,
                        "in_use": 8,
                        "queued": 4,
                        "max_per_node": 8
                    },
                    ...
                },
                "timestamp": "2026-01-20T18:30:00Z"
            }
        """
        return self._make_request("GET", "/v1/gpu/availability")

    def get_cluster_status(self) -> Dict[str, Any]:
        """
        Get overall cluster status and statistics
        
        Returns:
            {
                "total_gpus": 64,
                "available_gpus": 32,
                "in_use_gpus": 24,
                "queued_gpus": 8,
                "active_reservations": 5,
                "preparing_reservations": 1,
                "queued_reservations": 2,
                "pending_reservations": 0,
                "by_gpu_type": {
                    "h100": GPUTypeAvailability,
                    ...
                },
                "timestamp": "2026-01-20T18:30:00Z"
            }
        """
        return self._make_request("GET", "/v1/cluster/status")

    def create_disk(self, disk_name: str, size_gb: int = None):
        """Create a new persistent disk.
        
        Args:
            disk_name: Name of the disk to create
            size_gb: Optional disk size in GB
            
        Returns:
            dict with operation_id, disk_name, action, message, requested_at
            
        Example:
            {
                "operation_id": "abc-123",
                "disk_name": "my-disk",
                "action": "create",
                "message": "Disk creation request queued successfully",
                "requested_at": "2026-01-20T18:00:00Z"
            }
        """
        data = {"disk_name": disk_name}
        if size_gb:
            data["size_gb"] = size_gb
        return self._make_request("POST", "/v1/disks", json_data=data)

    def delete_disk(self, disk_name: str):
        """Delete a persistent disk (soft delete with 30-day retention).
        
        Args:
            disk_name: Name of the disk to delete
            
        Returns:
            dict with operation_id, disk_name, action, message, requested_at
            
        Example:
            {
                "operation_id": "abc-123",
                "disk_name": "my-disk",
                "action": "delete",
                "message": "Disk deletion request queued successfully. Will be deleted on 2026-02-19",
                "requested_at": "2026-01-20T18:00:00Z"
            }
        """
        return self._make_request("DELETE", f"/v1/disks/{disk_name}")

    def list_disks(self):
        """List all persistent disks for the current user.
        
        Returns:
            dict with disks (list) and total (int)
            
        Example:
            {
                "disks": [
                    {
                        "disk_name": "my-disk",
                        "user_id": "user@example.com",
                        "size_gb": 100,
                        "created_at": "2026-01-15T10:00:00Z",
                        "in_use": False,
                        "snapshot_count": 5
                    }
                ],
                "total": 1
            }
        """
        return self._make_request("GET", "/v1/disks")

    def get_disk_info(self, disk_name: str):
        """Get information about a specific disk.
        
        Args:
            disk_name: Name of the disk
            
        Returns:
            dict with disk information
            
        Example:
            {
                "disk_name": "my-disk",
                "user_id": "user@example.com",
                "size_gb": 100,
                "created_at": "2026-01-15T10:00:00Z",
                "in_use": False,
                "reservation_id": None,
                "snapshot_count": 5
            }
        """
        return self._make_request("GET", f"/v1/disks/{disk_name}")

    def get_disk_content(self, disk_name: str):
        """Get the contents of a disk's latest snapshot.
        
        Returns the ls -R output stored when the last snapshot was taken.
        This allows viewing disk contents without mounting the volume.
        
        Args:
            disk_name: Name of the disk
            
        Returns:
            dict with content information
            
        Example:
            {
                "disk_name": "my-disk",
                "content": "/home/user:\ntotal 12\n...",
                "s3_path": "s3://bucket/path/to/content.txt",
                "snapshot_date": "2026-01-20T10:00:00Z",
                "message": None
            }
            
        Or if no content available:
            {
                "disk_name": "my-disk",
                "content": None,
                "s3_path": None,
                "snapshot_date": None,
                "message": "No snapshot contents available..."
            }
        """
        return self._make_request("GET", f"/v1/disks/{disk_name}/content")

    def rename_disk(self, disk_name: str, new_name: str):
        """Rename a persistent disk.
        
        Updates the disk name in PostgreSQL and tags on all associated EBS snapshots.
        The disk must not be in use during the rename operation.
        
        Args:
            disk_name: Current name of the disk
            new_name: New name for the disk
            
        Returns:
            dict with rename results
            
        Example:
            {
                "message": "Disk renamed from 'old-name' to 'new-name' (3 snapshots updated)",
                "old_name": "old-name",
                "new_name": "new-name",
                "snapshots_updated": 3
            }
        """
        return self._make_request("POST", f"/v1/disks/{disk_name}/rename", 
                                   json_data={"new_name": new_name})

    def get_disk_operation_status(self, disk_name: str, operation_id: str):
        """Poll the status of a disk operation (create/delete).
        
        Args:
            disk_name: Name of the disk
            operation_id: Operation ID returned from create/delete
            
        Returns:
            dict with operation status
            
        Example:
            {
                "operation_id": "abc-123",
                "disk_name": "my-disk",
                "status": "completed",
                "error": None,
                "is_deleted": False,
                "delete_date": None,
                "created_at": "2026-01-20T10:00:00Z",
                "last_updated": "2026-01-20T10:01:00Z",
                "completed": True
            }
        """
        return self._make_request("GET", f"/v1/disks/{disk_name}/operations/{operation_id}")

