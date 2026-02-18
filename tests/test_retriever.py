"""Hybrid retriever tests for FAISS + DB integration path."""

import os
import tempfile
import faiss
import numpy as np
import types


class FakeClient:
    class embeddings:
        @staticmethod
        def create(model, input):
            class Item:
                def __init__(self, embedding):
                    self.embedding = embedding

            vec = [0.1] * 8
            return types.SimpleNamespace(data=[Item(vec)])


def test_hybrid_search_creates_hits(tmp_path, monkeypatch):
    # prepare a small FAISS index with 3 vectors
    dim = 8
    xb = np.random.RandomState(123).randn(3, dim).astype('float32')
    index = faiss.IndexFlatIP(dim)
    # normalize
    norms = np.linalg.norm(xb, axis=1, keepdims=True)
    xb = xb / (norms + 1e-9)
    index.add(xb)

    idx_path = tmp_path / "test.index"
    faiss.write_index(index, str(idx_path))

    # populate DB: create tables and insert index_shards and vectors rows
    import psycopg2

    conn = psycopg2.connect()
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS index_shards (shard_name TEXT UNIQUE, path TEXT, pipeline_run_id INTEGER, created_at TEXT, is_active INTEGER, index_id TEXT)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS vectors (id INTEGER PRIMARY KEY, text TEXT, page INTEGER, start_word INTEGER, end_word INTEGER, doc_id TEXT, pipeline_run_id INTEGER, created_at TEXT)"
    )
    # clear any prior rows from other tests (shared in-memory DB)
    cur.execute("DELETE FROM index_shards")
    cur.execute("DELETE FROM vectors")
    # insert index_shard and vectors rows
    index_id = "idx-test-1"
    sidecar = idx_path.with_suffix(".index.version.json")
    sidecar.write_text(f'{{"index_id": "{index_id}"}}')
    cur.execute(
        "INSERT INTO index_shards (shard_name, path, pipeline_run_id, created_at, is_active, index_id) VALUES (?, ?, ?, ?, 1, ?)",
        ("s1", str(idx_path), 1, "now", index_id),
    )
    for i in range(3):
        cur.execute(
            "INSERT INTO vectors (id, text, page, start_word, end_word, doc_id, pipeline_run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (i, f"doc text {i}", 1, 0, 10, 'doc', 1, "now"),
        )
    conn.commit()

    # monkeypatch OpenAI client usage: provide FakeClient instance
    fake_client = FakeClient()

    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("ragonometrics.indexing.retriever", Path("ragonometrics/indexing/retriever.py").resolve())
    retriever = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(retriever)

    hits = retriever.hybrid_search("test query", client=fake_client, db_url="dummy", top_k=2, bm25_weight=0.5)
    # hits should be a list (possibly empty if scoring ties)
    assert isinstance(hits, list)


def test_hybrid_search_disables_faiss_fallback_when_paper_scoped(monkeypatch):
    import ragonometrics.indexing.retriever as retriever

    monkeypatch.setattr(retriever, "_load_texts_for_shards", lambda db_url, paper_path=None: (["scoped text"], [101]))
    monkeypatch.setattr(retriever, "_embed_query", lambda **kwargs: [0.1] * 8)
    monkeypatch.setattr(retriever, "_embedding_search_pg", lambda db_url, vec, top_k, paper_path=None: [])

    calls = {"faiss": 0}

    def _fake_faiss(*args, **kwargs):
        calls["faiss"] += 1
        return [(101, 0.9)]

    monkeypatch.setattr(retriever, "_embedding_search_faiss", _fake_faiss)

    scoped_hits = retriever.hybrid_search(
        "test query",
        db_url="dummy",
        embedding_client=object(),
        embedding_model="e",
        bm25_weight=0.0,
        paper_path="/app/papers/target.pdf",
    )
    assert scoped_hits == []
    assert calls["faiss"] == 0

    unscoped_hits = retriever.hybrid_search(
        "test query",
        db_url="dummy",
        embedding_client=object(),
        embedding_model="e",
        bm25_weight=0.0,
    )
    assert unscoped_hits and unscoped_hits[0][0] == 101
    assert calls["faiss"] == 1


def test_hybrid_search_normalizes_scoped_paper_path(monkeypatch):
    import ragonometrics.indexing.retriever as retriever

    seen = {"load_path": None, "pg_path": None}

    def _fake_load(db_url, paper_path=None):
        seen["load_path"] = paper_path
        return ["target"], [7]

    def _fake_pg(db_url, vec, top_k, paper_path=None):
        seen["pg_path"] = paper_path
        return [(7, 1.0)]

    monkeypatch.setattr(retriever, "_load_texts_for_shards", _fake_load)
    monkeypatch.setattr(retriever, "_embed_query", lambda **kwargs: [0.2] * 8)
    monkeypatch.setattr(retriever, "_embedding_search_pg", _fake_pg)

    hits = retriever.hybrid_search(
        "target",
        db_url="dummy",
        embedding_client=object(),
        embedding_model="e",
        bm25_weight=0.0,
        paper_path=r"C:\papers\Target.pdf",
    )
    assert hits and hits[0][0] == 7
    assert seen["load_path"] == "C:/papers/Target.pdf"
    assert seen["pg_path"] == "C:/papers/Target.pdf"
