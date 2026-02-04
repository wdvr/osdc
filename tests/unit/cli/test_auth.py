"""
Unit tests for gpu_dev_cli.auth module

Tests:
- AWS authentication
- GitHub SSH key validation
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest


class TestAuthenticateUser:
    """Tests for authenticate_user function"""

    def test_authenticate_returns_user_info_on_success(self):
        """Should return user info when AWS auth succeeds"""
        mock_config = MagicMock()
        mock_config.get_user_identity.return_value = {
            "user_id": "AIDAEXAMPLE",
            "account": "123456789012",
            "arn": "arn:aws:iam::123456789012:user/testuser",
        }
        mock_config.get_queue_url.return_value = "https://sqs.us-east-2.amazonaws.com/123456789012/queue"
        mock_config.get_github_username.return_value = "githubuser"

        from gpu_dev_cli.auth import authenticate_user
        result = authenticate_user(mock_config)

        assert result["user_id"] == "testuser"
        assert result["github_user"] == "githubuser"
        assert "arn" in result

    def test_authenticate_raises_when_github_not_configured(self):
        """Should raise RuntimeError when github_user not set"""
        mock_config = MagicMock()
        mock_config.get_user_identity.return_value = {
            "user_id": "AIDAEXAMPLE",
            "account": "123456789012",
            "arn": "arn:aws:iam::123456789012:user/testuser",
        }
        mock_config.get_queue_url.return_value = "https://sqs.us-east-2.amazonaws.com/123456789012/queue"
        mock_config.get_github_username.return_value = None

        from gpu_dev_cli.auth import authenticate_user

        with pytest.raises(RuntimeError, match="GitHub username not configured"):
            authenticate_user(mock_config)

    def test_authenticate_raises_on_aws_error(self):
        """Should raise RuntimeError on AWS authentication failure"""
        mock_config = MagicMock()
        mock_config.get_user_identity.side_effect = Exception("Invalid credentials")

        from gpu_dev_cli.auth import authenticate_user

        with pytest.raises(RuntimeError, match="AWS authentication failed"):
            authenticate_user(mock_config)


class TestValidateSshKeyMatchesGithubUser:
    """Tests for validate_ssh_key_matches_github_user function"""

    def test_returns_valid_when_ssh_user_matches_config(self):
        """Should return valid=True when SSH user matches configured user"""
        mock_config = MagicMock()
        mock_config.get_github_username.return_value = "myuser"

        # Mock subprocess to return successful GitHub SSH response
        with patch("subprocess.run") as mock_run:
            # Create a mock tempfile
            with patch("tempfile.NamedTemporaryFile") as mock_tempfile:
                mock_file = MagicMock()
                mock_file.name = "/tmp/test"
                mock_file.__enter__ = MagicMock(return_value=mock_file)
                mock_file.__exit__ = MagicMock(return_value=False)

                # Simulate writing to temp file
                class MockTempFile:
                    name = "/tmp/test"

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def seek(self, pos):
                        pass

                    def read(self):
                        return "Hi myuser! You've successfully authenticated, but GitHub does not provide shell access."

                mock_tempfile.return_value = MockTempFile()
                mock_run.return_value = MagicMock(returncode=1)

                with patch("os.unlink"):
                    from gpu_dev_cli.auth import validate_ssh_key_matches_github_user
                    result = validate_ssh_key_matches_github_user(mock_config)

                    assert result["valid"] is True
                    assert result["configured_user"] == "myuser"
                    assert result["ssh_user"] == "myuser"

    def test_returns_invalid_when_ssh_user_differs(self):
        """Should return valid=False when SSH user doesn't match"""
        mock_config = MagicMock()
        mock_config.get_github_username.return_value = "configureduser"

        with patch("subprocess.run") as mock_run:
            with patch("tempfile.NamedTemporaryFile") as mock_tempfile:
                class MockTempFile:
                    name = "/tmp/test"

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def seek(self, pos):
                        pass

                    def read(self):
                        return "Hi differentuser! You've successfully authenticated, but GitHub does not provide shell access."

                mock_tempfile.return_value = MockTempFile()
                mock_run.return_value = MagicMock(returncode=1)

                with patch("os.unlink"):
                    from gpu_dev_cli.auth import validate_ssh_key_matches_github_user
                    result = validate_ssh_key_matches_github_user(mock_config)

                    assert result["valid"] is False
                    assert result["configured_user"] == "configureduser"
                    assert result["ssh_user"] == "differentuser"
                    assert "different" in result["error"].lower()

    def test_returns_error_when_github_not_configured(self):
        """Should return error when GitHub username not configured"""
        mock_config = MagicMock()
        mock_config.get_github_username.return_value = None

        from gpu_dev_cli.auth import validate_ssh_key_matches_github_user
        result = validate_ssh_key_matches_github_user(mock_config)

        assert result["valid"] is False
        assert result["error"] is not None
        assert "not configured" in result["error"]

    def test_returns_error_on_ssh_timeout(self):
        """Should return error when SSH connection times out"""
        mock_config = MagicMock()
        mock_config.get_github_username.return_value = "myuser"

        with patch("subprocess.run") as mock_run:
            with patch("tempfile.NamedTemporaryFile") as mock_tempfile:
                class MockTempFile:
                    name = "/tmp/test"

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                mock_tempfile.return_value = MockTempFile()
                mock_run.side_effect = subprocess.TimeoutExpired("ssh", 30)

                from gpu_dev_cli.auth import validate_ssh_key_matches_github_user
                result = validate_ssh_key_matches_github_user(mock_config)

                assert result["valid"] is False
                assert "timed out" in result["error"].lower()

    def test_case_insensitive_username_comparison(self):
        """Should compare usernames case-insensitively"""
        mock_config = MagicMock()
        mock_config.get_github_username.return_value = "MyUser"

        with patch("subprocess.run") as mock_run:
            with patch("tempfile.NamedTemporaryFile") as mock_tempfile:
                class MockTempFile:
                    name = "/tmp/test"

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def seek(self, pos):
                        pass

                    def read(self):
                        return "Hi myuser! You've successfully authenticated, but GitHub does not provide shell access."

                mock_tempfile.return_value = MockTempFile()
                mock_run.return_value = MagicMock(returncode=1)

                with patch("os.unlink"):
                    from gpu_dev_cli.auth import validate_ssh_key_matches_github_user
                    result = validate_ssh_key_matches_github_user(mock_config)

                    # Should be valid despite case difference
                    assert result["valid"] is True
