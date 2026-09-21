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


def test_get_pipeline_status_rejects_application_summary_item_name():
    fake_table = MagicMock()
    with patch.object(status_store, "_table", return_value=fake_table):
        result = status_store.get_pipeline_status("test-table", "APP#data-exporter")

    assert result is None
    fake_table.get_item.assert_not_called()


def test_get_pipeline_status_rejects_application_resource_item_name():
    fake_table = MagicMock()
    with patch.object(status_store, "_table", return_value=fake_table):
        result = status_store.get_pipeline_status(
            "test-table", "APPRESOURCE#data-exporter#arn:aws:states:us-east-1:1:stateMachine:connect_lake_load_batch"
        )

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


def test_get_all_statuses_filters_out_lineage_and_application_items():
    fake_table = MagicMock()
    fake_table.scan.return_value = {"Items": [{"pipeline_name": "athena_data_export"}]}
    with patch.object(status_store, "_table", return_value=fake_table):
        result = status_store.get_all_statuses("test-table")

    assert result == [{"pipeline_name": "athena_data_export"}]
    _, kwargs = fake_table.scan.call_args
    prefixes_filtered = set(kwargs["ExpressionAttributeValues"].values())
    assert prefixes_filtered == {"LINEAGE#", "APP#", "APPRESOURCE#"}
    assert kwargs["FilterExpression"].count("begins_with") == 3
