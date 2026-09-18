from src.application_registry import load_applications


def test_real_config_has_exactly_the_pilot_application():
    apps = load_applications()
    assert len(apps) == 1
    assert apps[0].id == "data-exporter"
    assert apps[0].stack_name == "data-exporter"
    assert apps[0].display_name == "Data Exporter"


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
