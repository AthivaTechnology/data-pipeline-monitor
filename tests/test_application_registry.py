from src.application_registry import load_applications


def test_real_config_has_all_six_confirmed_applications():
    apps = load_applications()
    by_id = {a.id: a for a in apps}
    assert len(apps) == 6
    assert by_id["data-exporter"].stack_name == "data-exporter"
    assert by_id["data-exporter"].display_name == "Data Exporter"
    assert by_id["leadgen-export-v2"].stack_name == "version2-athiva-leadgen-dynamodb-export-s3"
    assert by_id["leadgen-export-dev"].stack_name == "lhg-dev-stepfunctions-dynamodb-export-s3-multi-eventsrule"


def test_missing_file_behaves_like_empty(tmp_path):
    assert load_applications(tmp_path / "does-not-exist.yaml") == []


def test_malformed_yaml_degrades_to_empty_instead_of_raising(tmp_path):
    path = tmp_path / "applications.yaml"
    path.write_text("applications: [not valid: yaml: {{{", encoding="utf-8")
    assert load_applications(path) == []


def test_display_name_defaults_to_id_when_omitted(tmp_path):
    path = tmp_path / "applications.yaml"
    path.write_text("applications:\n  - id: my-app\n    stack_name: my-stack\n", encoding="utf-8")
    apps = load_applications(path)
    assert apps[0].display_name == "my-app"
