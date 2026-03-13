"""Minimal configuration for GPU Dev CLI - Zero setup required"""

import os
import json
from pathlib import Path
from typing import Dict, Any, Optional


class Config:
    """Zero-config configuration — AWS optional when GPU_DEV_API_URL is set

    Supports three modes:
    - "api": Traditional API-based mode (requires API service + port-forward)
    - "k8s-direct": Direct K8s mode (requires only KUBECONFIG, creates Jobs directly)
    """

    # Environment configurations
    ENVIRONMENTS = {
        "test": {
            "region": "us-west-1",
            "workspace": "default",
            "description": "Test environment",
            "api_url": None,  # Set after CloudFront deployment
        },
        "prod": {
            "region": "us-east-2",
            "workspace": "prod",
            "description": "Production environment",
            "api_url": None,  # Set after CloudFront deployment
        },
        "local": {
            "region": "local",
            "workspace": "local",
            "description": "Local / kubectl-based environment",
            "api_url": "http://localhost:8000",
        },
    }
    DEFAULT_ENVIRONMENT = "local"

    # Config file path (class-level for access without instantiation)
    CONFIG_FILE = Path.home() / ".config" / "gpu-dev" / "config.json"

    # Legacy paths for migration
    LEGACY_CONFIG_FILE = Path.home() / ".gpu-dev-config"
    LEGACY_ENVIRONMENT_FILE = Path.home() / ".gpu-dev-environment.json"

    def __init__(self):
        # Load unified config (handles migration from legacy files)
        self.user_config = self._load_config()

        # Get region from config, then AWS env vars, or default
        if self.user_config.get("region"):
            self.aws_region = self.user_config["region"]
        else:
            self.aws_region = os.getenv(
                "AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-2")
            )

        os.environ["AWS_DEFAULT_REGION"] = self.aws_region

        # Resource naming convention - no config needed!
        self.prefix = "pytorch-gpu-dev"

        # Construct resource names from convention
        self.reservations_table = f"{self.prefix}-reservations"
        self.disks_table = f"{self.prefix}-disks"
        self.availability_table = f"{self.prefix}-gpu-availability"
        self.cluster_name = f"{self.prefix}-cluster"

        # Skip AWS setup when API URL is set (non-AWS mode: MKS, local, etc.)
        self._aws_available = False
        self.session = None
        self._sts_client = None
        self._dynamodb = None

        if not os.getenv("GPU_DEV_API_URL") and self.user_config.get("environment") != "local":
            self.session = self._create_aws_session()
            self._aws_available = True

    def _create_aws_session(self):
        """Create AWS session with profile support"""
        try:
            import boto3
            # Try to use 'gpu-dev' profile if it exists
            session = boto3.Session(profile_name="gpu-dev")
            # Test if profile works by checking credentials
            session.get_credentials()
            return session
        except Exception:
            try:
                import boto3
                # Fall back to default credentials
                return boto3.Session()
            except ImportError:
                return None

    @property
    def sts_client(self):
        if not self._aws_available or not self.session:
            raise RuntimeError("AWS not available — set GPU_DEV_API_URL for non-AWS mode")
        if self._sts_client is None:
            self._sts_client = self.session.client("sts", region_name=self.aws_region)
        return self._sts_client

    @property
    def dynamodb(self):
        """
        DynamoDB resource for legacy disk operations.

        NOTE: This is only used by the persistent disk management system
        which still uses the legacy SQS/DynamoDB infrastructure.
        All job/reservation operations now use the API service.
        """
        if not self._aws_available or not self.session:
            raise RuntimeError("AWS not available — set GPU_DEV_API_URL for non-AWS mode")
        if self._dynamodb is None:
            self._dynamodb = self.session.resource(
                "dynamodb", region_name=self.aws_region
            )
        return self._dynamodb

    def get_user_identity(self) -> Dict[str, Any]:
        """Get current AWS user identity"""
        try:
            response = self.sts_client.get_caller_identity()
            return {
                "user_id": response["UserId"],
                "account": response["Account"],
                "arn": response["Arn"],
            }
        except Exception as e:
            raise RuntimeError(
                f"Cannot get AWS caller identity. Check AWS credentials: {e}"
            )

    def _load_config(self) -> Dict[str, Any]:
        """Load unified config from ~/.config/gpu-dev/config.json

        Migrates from legacy locations if needed:
        - ~/.gpu-dev-config (user config)
        - ~/.gpu-dev-environment.json (environment config)

        Ensures required environment keys exist, filling from defaults.
        """
        config = {}
        needs_save = False

        # Try to load existing config
        if self.CONFIG_FILE.exists():
            try:
                with open(self.CONFIG_FILE, "r") as f:
                    config = json.load(f)
            except Exception as e:
                print(f"Warning: Could not load config: {e}")
        else:
            # Migrate from legacy files
            migrated_from = []

            if self.LEGACY_CONFIG_FILE.exists():
                try:
                    with open(self.LEGACY_CONFIG_FILE, "r") as f:
                        config.update(json.load(f))
                    migrated_from.append(str(self.LEGACY_CONFIG_FILE))
                except Exception as e:
                    print(f"Warning: Could not read {self.LEGACY_CONFIG_FILE}: {e}")

            if self.LEGACY_ENVIRONMENT_FILE.exists():
                try:
                    with open(self.LEGACY_ENVIRONMENT_FILE, "r") as f:
                        config.update(json.load(f))
                    migrated_from.append(str(self.LEGACY_ENVIRONMENT_FILE))
                except Exception as e:
                    print(f"Warning: Could not read {self.LEGACY_ENVIRONMENT_FILE}: {e}")

            if migrated_from:
                print(
                    f"Migrated config from {', '.join(migrated_from)} "
                    f"to {self.CONFIG_FILE}"
                )
                needs_save = True

        # Ensure required environment keys exist
        default_env = self.ENVIRONMENTS[self.DEFAULT_ENVIRONMENT]
        if "region" not in config:
            config["region"] = default_env["region"]
            needs_save = True
        if "environment" not in config:
            config["environment"] = self.DEFAULT_ENVIRONMENT
            needs_save = True
        if "workspace" not in config:
            config["workspace"] = default_env["workspace"]
            needs_save = True

        # Save if we added any defaults or migrated
        if needs_save:
            try:
                self._save_config(config)
            except Exception as e:
                print(f"Warning: Could not save config: {e}")

        return config

    def _save_config(self, config: Dict[str, Any]) -> None:
        """Save config dict to file."""
        self.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

    def save_config(self, key: str, value: Any) -> None:
        """Save a configuration value."""
        self.user_config[key] = value
        self._save_config(self.user_config)

    def set_environment(self, env_name: str) -> Dict[str, Any]:
        """Set the environment (test or prod).

        Args:
            env_name: Environment name ('test' or 'prod')

        Returns:
            The environment config dict

        Raises:
            ValueError: If env_name is not valid

        Note:
            This does not invalidate cached AWS clients. In the CLI, this is
            fine since each command is a separate process. If using Config
            as a library, create a new instance after changing environments.
        """
        if env_name not in self.ENVIRONMENTS:
            raise ValueError(
                f"Invalid environment: {env_name}. "
                f"Must be one of: {list(self.ENVIRONMENTS.keys())}"
            )

        env_config = self.ENVIRONMENTS[env_name]

        # Update config with environment settings
        self.user_config["environment"] = env_name
        self.user_config["region"] = env_config["region"]
        self.user_config["workspace"] = env_config["workspace"]

        # Sync api_url from environment defaults so the explicit config
        # value doesn't override the environment-specific default
        if env_config.get("api_url"):
            self.user_config["api_url"] = env_config["api_url"]
        else:
            # Clear any previous api_url so the environment default is used
            self.user_config.pop("api_url", None)

        self._save_config(self.user_config)
        self.aws_region = env_config["region"]
        os.environ["AWS_DEFAULT_REGION"] = self.user_config["region"]

        return env_config

    def get(self, key: str) -> Optional[Any]:
        """Get a config value."""
        return self.user_config.get(key)

    @property
    def mode(self) -> str:
        """Determine CLI operating mode.

        Returns "k8s-direct" when:
        - GPU_DEV_MODE=k8s-direct is explicitly set, OR
        - KUBECONFIG is set and environment is "local" (no API URL configured)

        Returns "api" otherwise (traditional API-based mode).
        """
        # Explicit mode override
        explicit_mode = os.getenv("GPU_DEV_MODE", "").lower()
        if explicit_mode == "k8s-direct":
            return "k8s-direct"
        if explicit_mode == "api":
            return "api"

        # Auto-detect: KUBECONFIG set + local env + no explicit API URL
        if os.getenv("KUBECONFIG"):
            env = self.user_config.get("environment", "local")
            has_api_url = bool(os.getenv("GPU_DEV_API_URL") or self.user_config.get("api_url"))
            if env == "local" and not has_api_url:
                return "k8s-direct"

        return "api"

    @property
    def kubeconfig_path(self) -> Optional[str]:
        """Get kubeconfig path from $KUBECONFIG or default."""
        return os.getenv("KUBECONFIG") or str(Path.home() / ".kube" / "config")

    @property
    def namespace(self) -> str:
        """Get K8s namespace for gpu-dev resources."""
        return os.getenv("GPU_DEV_NAMESPACE", "gpu-dev")

    def get_github_username(self) -> Optional[str]:
        """Get GitHub username from config."""
        return self.user_config.get("github_user")


def load_config() -> Config:
    """Load zero-config setup"""
    return Config()
