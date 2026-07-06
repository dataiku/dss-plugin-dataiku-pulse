from __future__ import annotations

from data_collection.contracts import (
    ContractIssue,
    format_report,
    load_flatten_registry,
    validate_all,
    validate_casting_columns,
)


def test_validate_all_has_no_errors():
    """The shipped contracts must be consistent (warnings are allowed)."""

    issues = validate_all()
    errors = [i for i in issues if i.severity == "error"]
    assert not errors, format_report(errors)


def test_casting_catches_unknown_columns(tmp_path, monkeypatch):
    """A casting entry matching no flatten column is the P0.1 bug class."""

    registry = load_flatten_registry()
    # Simulate the historical bug: singular column names that no flatten
    # config produces must be flagged as errors.
    registry["project"][("fake", "project_metadata")] = ["datasets_name"]

    import data_collection.contracts as contracts

    casting_dir = tmp_path / "casting_columns"
    casting_dir.mkdir()
    (casting_dir / "numeric.yaml").write_text("- dataset_versiontag_versionnumber\n")
    monkeypatch.setattr(
        contracts, "_schema_consistency_dir", lambda: tmp_path
    )

    issues = validate_casting_columns(registry)
    assert any(
        i.severity == "error" and "dataset_versiontag_versionnumber" in i.message
        for i in issues
    )


def test_flatten_registry_shape():
    registry = load_flatten_registry()
    assert set(registry) == {"project", "instance", "audit"}
    assert ("datasets", "project_metadata") in registry["project"]
    columns = registry["project"][("datasets", "project_metadata")]
    assert "datasets_versiontag_versionnumber" in columns
    assert all(isinstance(i, ContractIssue) for i in validate_casting_columns(registry))
