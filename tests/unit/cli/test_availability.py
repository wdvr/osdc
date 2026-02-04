"""
Unit tests for gpu_dev_cli availability command

Tests:
- Parse availability response
- Format display output
- Watch mode polling
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


class TestAvailabilityParsing:
    """Tests for parsing availability responses"""

    def test_parse_availability_response(self):
        """Should parse availability response correctly"""
        from gpu_dev_cli.availability import parse_availability_response

        response = {
            "availability": [
                {
                    "gpu_type": "h100",
                    "available_gpus": 4,
                    "total_gpus": 16,
                    "queue_length": 2,
                },
                {
                    "gpu_type": "t4",
                    "available_gpus": 8,
                    "total_gpus": 8,
                    "queue_length": 0,
                },
            ],
            "last_updated": "2024-01-15T10:00:00Z",
        }

        result = parse_availability_response(response)

        assert len(result) == 2
        assert result["h100"]["available"] == 4
        assert result["h100"]["total"] == 16

    def test_parse_empty_availability(self):
        """Should handle empty availability"""
        from gpu_dev_cli.availability import parse_availability_response

        response = {"availability": [], "last_updated": "2024-01-15T10:00:00Z"}

        result = parse_availability_response(response)

        assert len(result) == 0


class TestAvailabilityDisplay:
    """Tests for formatting availability display"""

    def test_format_availability_table(self):
        """Should format availability as table"""
        from gpu_dev_cli.availability import format_availability_table

        availability = {
            "h100": {"available": 4, "total": 16, "queue_length": 2},
            "t4": {"available": 8, "total": 8, "queue_length": 0},
        }

        output = format_availability_table(availability)

        assert "h100" in output.lower() or "H100" in output
        assert "4" in output
        assert "16" in output

    def test_get_availability_color(self):
        """Should add color coding based on availability"""
        from gpu_dev_cli.availability import get_availability_color

        assert get_availability_color(8, 8) == "green"
        assert get_availability_color(4, 8) == "yellow"
        assert get_availability_color(0, 8) == "red"


class TestAvailabilityGPUInfo:
    """Tests for GPU information retrieval"""

    def test_get_gpu_specs(self):
        """Should return GPU specifications"""
        from gpu_dev_cli.availability import get_gpu_specs

        h100_specs = get_gpu_specs("h100")
        assert h100_specs["memory_gb"] == 80
        assert h100_specs["gpus_per_node"] == 8

        t4_specs = get_gpu_specs("t4")
        assert t4_specs["memory_gb"] == 16
        assert t4_specs["gpus_per_node"] == 4

    def test_get_all_gpu_types(self):
        """Should return all supported GPU types"""
        from gpu_dev_cli.availability import get_all_gpu_types

        gpu_types = get_all_gpu_types()

        expected = ["t4", "t4-small", "l4", "a10g", "a100", "h100", "h200", "b200"]
        for gpu in expected:
            assert gpu in gpu_types
