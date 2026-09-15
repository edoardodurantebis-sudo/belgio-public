from belgium_public.config import load_registry


def test_registry_unique_and_no_internal_sources():
    sources, _ = load_registry()
    ids = [s.id for s in sources]
    assert len(ids) == len(set(ids))
    assert all(s.provider in {"Elia", "JAO", "ENTSO-E"} for s in sources)
    banned = "duferco"
    assert all(banned not in (s.endpoint + s.documentation + s.notes).lower() for s in sources)


def test_core_sources_validate():
    sources, _ = load_registry()
    core = [s for s in sources if s.tier == "core"]
    assert core
    assert all(s.validated for s in core)
    assert {"ods134", "ods127", "ods132", "ods166"}.issubset({s.id for s in core})
