"""Metadata DB helper tests for pipeline run creation and shard publishing."""

import importlib.util
from pathlib import Path


def _load_mod(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, Path(path).resolve())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


metadata = _load_mod("ragonometrics/indexing/metadata.py", "ragonometrics.indexing.metadata")


def test_pipeline_run_and_shard_publish():
    db_url = "dummy"
    conn = metadata.init_metadata_db(db_url)
    run_id = metadata.create_pipeline_run(conn, git_sha="deadbeef", extractor_version="poppler-23", embedding_model="emb-1", chunk_words=256, chunk_overlap=32, normalized=True)
    assert isinstance(run_id, int)

    shard_id = metadata.publish_shard(conn, "shard-test", "/tmp/shard-test.index", run_id)
    assert isinstance(shard_id, int)

    shards = metadata.get_active_shards(conn)
    assert len(shards) == 1
    assert shards[0][0] == "shard-test"


def test_pipeline_run_idempotency():
    db_url = "dummy"
    conn = metadata.init_metadata_db(db_url)
    run_id1 = metadata.create_pipeline_run(
        conn,
        git_sha="deadbeef",
        extractor_version="poppler-23",
        embedding_model="emb-1",
        chunk_words=256,
        chunk_overlap=32,
        normalized=True,
        idempotency_key="same-key",
    )
    run_id2 = metadata.create_pipeline_run(
        conn,
        git_sha="deadbeef",
        extractor_version="poppler-23",
        embedding_model="emb-1",
        chunk_words=256,
        chunk_overlap=32,
        normalized=True,
        idempotency_key="same-key",
    )
    assert run_id1 == run_id2


def test_upsert_paper_metadata():
    db_url = "dummy"
    conn = metadata.init_metadata_db(db_url)
    metadata.upsert_paper_metadata(
        conn,
        doc_id="doc-1",
        path="/tmp/paper-a.pdf",
        title="Paper A",
        author="Alice Author",
        authors=["Alice Author", "Bob Writer"],
        primary_doi="10.1234/example.1",
        dois=["10.1234/example.1", "10.1234/example.2"],
        openalex_id="https://openalex.org/W123",
        publication_year=2020,
        venue="Example Journal",
        repec_handle="RePEc:abc:def:123",
        source_url="https://example.org/paper-a",
        metadata_json={"k": "v"},
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT title, author, primary_doi, publication_year, venue FROM paper_metadata WHERE doc_id = %s",
        ("doc-1",),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "Paper A"
    assert row[1] == "Alice Author"
    assert row[2] == "10.1234/example.1"
    assert int(row[3]) == 2020
    assert row[4] == "Example Journal"

    metadata.upsert_paper_metadata(
        conn,
        doc_id="doc-1",
        path="/tmp/paper-a.pdf",
        title="Paper A Updated",
        author="Alice Author",
        primary_doi="10.1234/example.9",
    )
    cur.execute("SELECT title, primary_doi FROM paper_metadata WHERE doc_id = %s", ("doc-1",))
    row2 = cur.fetchone()
    assert row2 is not None
    assert row2[0] == "Paper A Updated"
    assert row2[1] == "10.1234/example.9"
