"""Regression tests for preserving profiling placement across queue transitions."""

import json
from unittest.mock import MagicMock


NODE_LABELS = {"nsight": "true"}


def test_single_node_initial_record_preserves_node_labels(
    monkeypatch, lambda_index, aws_mocks
):
    table = MagicMock()
    aws_mocks["dynamodb"].Table.return_value = table
    monkeypatch.setattr(
        lambda_index, "validate_reservation_request", lambda _request: (False, "stop")
    )
    monkeypatch.setattr(lambda_index, "update_reservation_status", MagicMock())

    lambda_index.process_reservation_request(
        {
            "body": json.dumps(
                {
                    "reservation_id": "res-single",
                    "user_id": "user",
                    "gpu_type": "b200",
                    "gpu_count": 1,
                    "duration_hours": 1,
                    "node_labels": NODE_LABELS,
                }
            )
        }
    )

    assert table.put_item.call_args.kwargs["Item"]["node_labels"] == NODE_LABELS


def test_multinode_initial_record_preserves_node_labels(
    monkeypatch, lambda_index, aws_mocks
):
    table = MagicMock()
    aws_mocks["dynamodb"].Table.return_value = table
    monkeypatch.setattr(lambda_index, "check_all_multinode_nodes_ready", lambda *_: False)

    assert lambda_index.process_multinode_reservation_request(
        {
            "reservation_id": "res-multi",
            "master_reservation_id": "master",
            "user_id": "user",
            "gpu_type": "b200",
            "gpu_count": 8,
            "total_gpu_count": 16,
            "total_nodes": 2,
            "duration_hours": 1,
            "node_labels": NODE_LABELS,
        }
    )

    assert table.put_item.call_args.kwargs["Item"]["node_labels"] == NODE_LABELS


def test_final_reservation_record_preserves_node_labels(lambda_index, aws_mocks):
    table = MagicMock()
    aws_mocks["dynamodb"].Table.return_value = table

    reservation_id = lambda_index.create_reservation(
        {
            "reservation_id": "res-final",
            "user_id": "user",
            "gpu_type": "b200",
            "gpu_count": 1,
            "duration_hours": 1,
            "node_labels": NODE_LABELS,
        }
    )

    assert reservation_id == "res-final"
    assert table.put_item.call_args.kwargs["Item"]["node_labels"] == NODE_LABELS
