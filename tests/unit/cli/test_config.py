"""
Unit tests for gpu_dev_cli.config module

Tests:
- Config initialization and migration
- Environment switching
- Config file operations
- AWS session creation
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestConfigInit:
    """Tests for Config class initialization"""

    def test_config_creates_default_file_if_missing(self, tmp_path):
        """Config should create config.json with defaults if not exists"""
        config_file = tmp_path / ".config" / "gpu-dev" / "config.json"

        with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
            with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", tmp_path / ".gpu-dev-config"):
                with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", tmp_path / ".gpu-dev-environment.json"):
                    with patch("boto3.Session"):
                        from gpu_dev_cli.config import Config
                        config = Config()

                        assert config_file.exists()
                        saved_config = json.loads(config_file.read_text())
                        assert "region" in saved_config
                        assert "environment" in saved_config

    def test_config_loads_existing_file(self, tmp_path):
        """Config should load existing config.json"""
        config_file = tmp_path / ".config" / "gpu-dev" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({
            "github_user": "myuser",
            "region": "eu-west-1",
            "environment": "prod",
            "workspace": "prod",
        }))

        with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
            with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", tmp_path / ".gpu-dev-config"):
                with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", tmp_path / ".gpu-dev-environment.json"):
                    with patch("boto3.Session"):
                        from gpu_dev_cli.config import Config
                        config = Config()

                        assert config.get_github_username() == "myuser"
                        assert config.aws_region == "eu-west-1"

    def test_config_migrates_legacy_files(self, tmp_path):
        """Config should migrate from legacy config files"""
        config_file = tmp_path / ".config" / "gpu-dev" / "config.json"
        legacy_config = tmp_path / ".gpu-dev-config"
        legacy_env = tmp_path / ".gpu-dev-environment.json"

        # Create legacy files
        legacy_config.write_text(json.dumps({"github_user": "legacyuser"}))
        legacy_env.write_text(json.dumps({"region": "ap-northeast-1"}))

        with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
            with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", legacy_config):
                with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", legacy_env):
                    with patch("boto3.Session"):
                        from gpu_dev_cli.config import Config
                        config = Config()

                        # Should have migrated values
                        assert config.get_github_username() == "legacyuser"
                        assert config.aws_region == "ap-northeast-1"


class TestConfigEnvironments:
    """Tests for environment switching"""

    def test_set_environment_updates_region(self, tmp_path):
        """set_environment should update region based on env"""
        config_file = tmp_path / ".config" / "gpu-dev" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({
            "region": "us-east-2",
            "environment": "prod",
            "workspace": "prod",
        }))

        with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
            with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", tmp_path / ".gpu-dev-config"):
                with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", tmp_path / ".gpu-dev-environment.json"):
                    with patch("boto3.Session"):
                        from gpu_dev_cli.config import Config
                        config = Config()

                        result = config.set_environment("test")

                        assert config.aws_region == "us-west-1"
                        assert result["region"] == "us-west-1"

    def test_set_invalid_environment_raises(self, tmp_path):
        """set_environment should raise ValueError for invalid env"""
        config_file = tmp_path / ".config" / "gpu-dev" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({
            "region": "us-east-2",
            "environment": "prod",
            "workspace": "prod",
        }))

        with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
            with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", tmp_path / ".gpu-dev-config"):
                with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", tmp_path / ".gpu-dev-environment.json"):
                    with patch("boto3.Session"):
                        from gpu_dev_cli.config import Config
                        config = Config()

                        with pytest.raises(ValueError, match="Invalid environment"):
                            config.set_environment("staging")


class TestConfigOperations:
    """Tests for config save/load operations"""

    def test_save_config_persists_value(self, tmp_path):
        """save_config should write to file"""
        config_file = tmp_path / ".config" / "gpu-dev" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({
            "region": "us-east-2",
            "environment": "prod",
            "workspace": "prod",
        }))

        with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
            with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", tmp_path / ".gpu-dev-config"):
                with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", tmp_path / ".gpu-dev-environment.json"):
                    with patch("boto3.Session"):
                        from gpu_dev_cli.config import Config
                        config = Config()

                        config.save_config("github_user", "newuser")

                        # Re-read file
                        saved = json.loads(config_file.read_text())
                        assert saved["github_user"] == "newuser"

    def test_get_returns_saved_value(self, tmp_path):
        """get should return saved config value"""
        config_file = tmp_path / ".config" / "gpu-dev" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({
            "github_user": "saveduser",
            "region": "us-east-2",
            "environment": "prod",
            "workspace": "prod",
        }))

        with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
            with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", tmp_path / ".gpu-dev-config"):
                with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", tmp_path / ".gpu-dev-environment.json"):
                    with patch("boto3.Session"):
                        from gpu_dev_cli.config import Config
                        config = Config()

                        assert config.get("github_user") == "saveduser"
                        assert config.get("nonexistent") is None


class TestAWSSession:
    """Tests for AWS session creation"""

    def test_creates_session_with_gpu_dev_profile_if_available(self, tmp_path):
        """Should try gpu-dev profile first"""
        config_file = tmp_path / ".config" / "gpu-dev" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({
            "region": "us-east-2",
            "environment": "prod",
            "workspace": "prod",
        }))

        mock_session = MagicMock()
        mock_session.get_credentials.return_value = MagicMock()

        with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
            with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", tmp_path / ".gpu-dev-config"):
                with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", tmp_path / ".gpu-dev-environment.json"):
                    with patch("boto3.Session", return_value=mock_session) as mock_session_class:
                        from gpu_dev_cli.config import Config
                        config = Config()

                        # First call should be with gpu-dev profile
                        mock_session_class.assert_any_call(profile_name="gpu-dev")

    def test_falls_back_to_default_session(self, tmp_path):
        """Should fall back to default session if gpu-dev profile fails"""
        config_file = tmp_path / ".config" / "gpu-dev" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({
            "region": "us-east-2",
            "environment": "prod",
            "workspace": "prod",
        }))

        def session_side_effect(*args, **kwargs):
            if kwargs.get("profile_name") == "gpu-dev":
                raise Exception("Profile not found")
            return MagicMock()

        with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
            with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", tmp_path / ".gpu-dev-config"):
                with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", tmp_path / ".gpu-dev-environment.json"):
                    with patch("boto3.Session", side_effect=session_side_effect):
                        from gpu_dev_cli.config import Config
                        config = Config()

                        # Should have created a session (fell back to default)
                        assert config.session is not None


class TestResourceNaming:
    """Tests for resource naming conventions"""

    def test_resource_names_use_prefix(self, tmp_path):
        """Resource names should use pytorch-gpu-dev prefix"""
        config_file = tmp_path / ".config" / "gpu-dev" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({
            "region": "us-east-2",
            "environment": "prod",
            "workspace": "prod",
        }))

        with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
            with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", tmp_path / ".gpu-dev-config"):
                with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", tmp_path / ".gpu-dev-environment.json"):
                    with patch("boto3.Session"):
                        from gpu_dev_cli.config import Config
                        config = Config()

                        assert config.prefix == "pytorch-gpu-dev"
                        # Note: queue_name removed - dev branch uses PGMQ (PostgreSQL queue)
                        # instead of SQS, managed by API service not CLI
                        assert "pytorch-gpu-dev" in config.cluster_name
