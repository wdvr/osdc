"""Minimal configuration for GPU Dev CLI - Zero setup required"""

import os
import json
import boto3
from pathlib import Path
from typing import Dict, Any, Optional


class Config:
    """Zero-config AWS-based configuration"""

    def __init__(self):
        # Config file paths
        self.config_file = Path.home() / ".gpu-dev-config"
        self.environment_config_file = Path.home() / ".config" / ".gpu-dev-environment.json"

        # Load environment config first to get region
        self.environment_config = self._load_environment_config()

        # Get region from environment config file, then AWS env vars, or default
        if self.environment_config.get("region"):
            self.aws_region = self.environment_config["region"]
        else:
            self.aws_region = os.getenv(
                "AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-2")
            )

        # Resource naming convention - no config needed!
        self.prefix = "pytorch-gpu-dev"

        # Construct ARNs from convention
        self.queue_name = f"{self.prefix}-reservation-queue"
        self.reservations_table = f"{self.prefix}-reservations"
        self.disks_table = f"{self.prefix}-disks"
        self.availability_table = f"{self.prefix}-gpu-availability"
        self.cluster_name = f"{self.prefix}-cluster"

        # Determine AWS session (with profile support)
        self.session = self._create_aws_session()

        # AWS clients
        self._sts_client = None
        self._sqs_client = None
        self._dynamodb = None

        # Load user config
        self.user_config = self._load_user_config()

    def _create_aws_session(self):
        """Create AWS session with profile support"""
        try:
            # Try to use 'gpu-dev' profile if it exists
            session = boto3.Session(profile_name="gpu-dev")
            # Test if profile works by checking credentials
            session.get_credentials()
            return session
        except Exception:
            # Fall back to default credentials (environment, default profile, IAM role, etc.)
            return boto3.Session()

    @property
    def sts_client(self):
        if self._sts_client is None:
            self._sts_client = self.session.client("sts", region_name=self.aws_region)
        return self._sts_client

    @property
    def sqs_client(self):
        if self._sqs_client is None:
            self._sqs_client = self.session.client("sqs", region_name=self.aws_region)
        return self._sqs_client

    @property
    def dynamodb(self):
        if self._dynamodb is None:
            self._dynamodb = self.session.resource(
                "dynamodb", region_name=self.aws_region
            )
        return self._dynamodb

    def get_queue_url(self) -> str:
        """Get SQS queue URL by name"""
        try:
            response = self.sqs_client.get_queue_url(QueueName=self.queue_name)
            return response["QueueUrl"]
        except Exception as e:
            raise RuntimeError(
                f"Cannot access SQS queue {self.queue_name}. Check AWS permissions: {e}"
            )

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

    def _load_environment_config(self) -> Dict[str, Any]:
        """Load environment configuration from ~/.config/.gpu-dev-environment.json

        Migrates from legacy location ~/.gpu-dev-environment.json if needed.
        Creates the file with default region if it doesn't exist.
        """
        legacy_config_file = Path.home() / ".gpu-dev-environment.json"

        # If new config exists, use it
        if self.environment_config_file.exists():
            try:
                with open(self.environment_config_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(
                    f"Warning: Could not load environment config: {e}"
                )
                return {}

        # Check for legacy config and migrate
        if legacy_config_file.exists():
            try:
                with open(legacy_config_file, "r") as f:
                    config = json.load(f)
                # Migrate to new location
                self.environment_config_file.parent.mkdir(
                    parents=True, exist_ok=True
                )
                with open(self.environment_config_file, "w") as f:
                    json.dump(config, f, indent=2)
                print(
                    f"Migrated config from {legacy_config_file} "
                    f"to {self.environment_config_file}"
                )
                return config
            except Exception as e:
                print(f"Warning: Could not migrate legacy config: {e}")
                return {}

        # No config exists, create default with region
        default_config = {"region": "us-east-2"}
        try:
            self.environment_config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.environment_config_file, "w") as f:
                json.dump(default_config, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not create environment config file: {e}")
        return default_config

    def _load_user_config(self) -> Dict[str, Any]:
        """Load user configuration from ~/.gpu-dev-config"""
        if not self.config_file.exists():
            return {}

        try:
            with open(self.config_file, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load config file {self.config_file}: {e}")
            return {}

    def save_user_config(self, key: str, value: str) -> None:
        """Save a configuration value to ~/.gpu-dev-config"""
        # Update in-memory config
        self.user_config[key] = value

        # Save to file
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.user_config, f, indent=2)
        except Exception as e:
            raise RuntimeError(f"Could not save config to {self.config_file}: {e}")

    def get_github_username(self) -> Optional[str]:
        """Get GitHub username from config"""
        return self.user_config.get("github_user")

    def get_user_config_value(self, key: str) -> Optional[str]:
        """Get any config value"""
        return self.user_config.get(key)


def load_config() -> Config:
    """Load zero-config setup"""
    return Config()
