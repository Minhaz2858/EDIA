from backend.src.skills.background.ontology_integrity_monitor import OntologyIntegrityMonitor


def test_ontology_integrity_monitor_runs_and_returns_shape():
    result = OntologyIntegrityMonitor().run()

    assert result.skill_name == "ontology_integrity_monitor"
    assert result.status in {"success", "error"}
    assert isinstance(result.summary, str)
    assert isinstance(result.data, dict)

    assert "graph_ids_count" in result.data
    assert "resolver_ids_count" in result.data
    assert "aliases_count" in result.data
    assert "missing_in_graph" in result.data
    assert "uncovered_graph_ids" in result.data
    assert "duplicate_alias_maps" in result.data
