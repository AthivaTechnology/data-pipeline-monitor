from unittest.mock import MagicMock, patch

from src import status_store


def test_get_pipeline_status_rejects_lineage_prefixed_name_without_touching_dynamodb():
    fake_table = MagicMock()
    with patch.object(status_store, "_table", return_value=fake_table):
        result = status_store.get_pipeline_status("test-table", "LINEAGE#SUMMARY")

    assert result is None
    fake_table.get_item.assert_not_called()


def test_get_pipeline_status_rejects_lineage_pipeline_item_name():
    fake_table = MagicMock()
    with patch.object(status_store, "_table", return_value=fake_table):
        result = status_store.get_pipeline_status("test-table", "LINEAGE#PIPELINE#athena_data_export")

    assert result is None
    fake_table.get_item.assert_not_called()


def test_get_pipeline_status_still_works_for_a_real_pipeline_name():
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": {"pipeline_name": "athena_data_export", "execution_status": "fresh"}}
    with patch.object(status_store, "_table", return_value=fake_table):
        result = status_store.get_pipeline_status("test-table", "athena_data_export")

    assert result == {"pipeline_name": "athena_data_export", "execution_status": "fresh"}
    fake_table.get_item.assert_called_once_with(Key={"pipeline_name": "athena_data_export"})


def test_get_pipeline_status_returns_none_for_a_real_missing_pipeline():
    fake_table = MagicMock()
    fake_table.get_item.return_value = {}
    with patch.object(status_store, "_table", return_value=fake_table):
        result = status_store.get_pipeline_status("test-table", "does_not_exist")

    assert result is None


def test_get_all_statuses_filters_out_lineage_items():
    fake_table = MagicMock()
    fake_table.scan.return_value = {"Items": [{"pipeline_name": "athena_data_export"}]}
    with patch.object(status_store, "_table", return_value=fake_table):
        result = status_store.get_all_statuses("test-table")

    assert result == [{"pipeline_name": "athena_data_export"}]
    _, kwargs = fake_table.scan.call_args
    assert "LINEAGE#" in kwargs["ExpressionAttributeValues"][":prefix"]
    assert "begins_with" in kwargs["FilterExpression"]
