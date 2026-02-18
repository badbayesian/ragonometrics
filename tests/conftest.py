"""Pytest bootstrap stubs for external deps and sqlite-backed DB shims."""

import sys
import types
import sqlite3
import re


class SQLiteCursorWrapper:
    def __init__(self, cur):
        self._cur = cur

    @staticmethod
    def _rewrite_sql(sql: str) -> str:
        out = sql
        # Drop schema qualifiers for sqlite-backed tests.
        out = re.sub(r"\b(?:ingestion|indexing|workflow|retrieval|observability|enrichment|auth)\.", "", out)
        # Remove Postgres casts.
        out = re.sub(r"::[A-Za-z_][A-Za-z0-9_]*", "", out)
        # sqlite compatibility for NOW()/booleans/null ordering.
        out = out.replace("NOW()", "CURRENT_TIMESTAMP")
        out = re.sub(r"\bTRUE\b", "1", out, flags=re.IGNORECASE)
        out = re.sub(r"\bFALSE\b", "0", out, flags=re.IGNORECASE)
        out = re.sub(r"\s+NULLS\s+LAST\b", "", out, flags=re.IGNORECASE)
        # Ignore unsupported schema/extension statements in sqlite tests.
        if re.search(r"^\s*CREATE\s+SCHEMA\b", out, flags=re.IGNORECASE):
            return "SELECT 1"
        if re.search(r"^\s*CREATE\s+EXTENSION\b", out, flags=re.IGNORECASE):
            return "SELECT 1"
        if "to_regclass('public.alembic_version')" in out:
            return "SELECT 'alembic_version'"
        # Replace unsupported vector/GIN ANN index syntax with basic sqlite index DDL.
        out = re.sub(r"\bVECTOR\b", "BLOB", out, flags=re.IGNORECASE)
        out = re.sub(r"\s+USING\s+GIN\s*\([^)]*\)", "", out, flags=re.IGNORECASE)
        out = re.sub(r"\s+USING\s+diskann\s*\([^)]*\)", "", out, flags=re.IGNORECASE)
        out = re.sub(r"\s+USING\s+ivfflat\s*\([^)]*\)\s*WITH\s*\([^)]*\)", "", out, flags=re.IGNORECASE)
        out = out.replace(" jsonb_path_ops", "")
        return out

    def execute(self, sql, params=None):
        if params is None:
            params = ()
        # translate psycopg2 %s params to sqlite ? params
        sql2 = self._rewrite_sql(sql).replace("%s", "?")
        return self._cur.execute(sql2, params)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SQLiteConnWrapper:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self.info = types.SimpleNamespace(dsn="sqlite://memory")
        cur = self._conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num TEXT PRIMARY KEY)")
        cur.execute("DELETE FROM alembic_version")
        cur.execute("INSERT INTO alembic_version(version_num) VALUES ('0006')")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS run_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                record_kind TEXT NOT NULL,
                step TEXT NOT NULL DEFAULT '',
                record_key TEXT NOT NULL DEFAULT '',
                status TEXT,
                papers_dir TEXT,
                config_hash TEXT,
                workstream_id TEXT,
                arm TEXT,
                parent_run_id TEXT,
                trigger_source TEXT,
                git_sha TEXT,
                git_branch TEXT,
                config_effective_json TEXT DEFAULT '{}',
                paper_set_hash TEXT,
                question TEXT,
                report_question_set TEXT,
                started_at TEXT,
                finished_at TEXT,
                report_path TEXT,
                agentic_status TEXT,
                report_hash TEXT,
                report_question_count INTEGER,
                confidence_mean REAL,
                confidence_label_counts_json TEXT DEFAULT '{}',
                final_answer_hash TEXT,
                question_id TEXT,
                artifact_type TEXT,
                artifact_path TEXT,
                artifact_sha256 TEXT,
                idempotency_key TEXT,
                input_hash TEXT,
                reuse_source_run_id TEXT,
                reuse_source_record_key TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                output_json TEXT DEFAULT '{}',
                payload_json TEXT DEFAULT '{}',
                metadata_json TEXT DEFAULT '{}',
                UNIQUE(run_id, record_kind, step, record_key)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS query_cache (
                cache_key TEXT PRIMARY KEY,
                query TEXT,
                paper_path TEXT,
                model TEXT,
                context_hash TEXT,
                answer TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                model TEXT,
                operation TEXT,
                step TEXT,
                question_id TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                session_id TEXT,
                request_id TEXT,
                provider_request_id TEXT,
                latency_ms INTEGER,
                cache_hit INTEGER,
                cost_usd_input REAL,
                cost_usd_output REAL,
                cost_usd_total REAL,
                run_id TEXT,
                meta TEXT DEFAULT '{}'
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                path TEXT,
                title TEXT,
                author TEXT,
                extracted_at TEXT,
                file_hash TEXT,
                text_hash TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_metadata (
                doc_id TEXT PRIMARY KEY,
                path TEXT,
                title TEXT,
                author TEXT,
                authors_json TEXT DEFAULT '[]',
                primary_doi TEXT,
                dois_json TEXT DEFAULT '[]',
                openalex_id TEXT,
                openalex_doi TEXT,
                publication_year INTEGER,
                venue TEXT,
                repec_handle TEXT,
                source_url TEXT,
                openalex_json TEXT DEFAULT '{}',
                citec_json TEXT DEFAULT '{}',
                metadata_json TEXT DEFAULT '{}',
                extracted_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_run_id TEXT,
                workstream_id TEXT,
                arm TEXT,
                paper_set_hash TEXT,
                index_build_reason TEXT,
                git_sha TEXT,
                extractor_version TEXT,
                embedding_model TEXT,
                chunk_words INTEGER,
                chunk_overlap INTEGER,
                normalized INTEGER,
                idempotency_key TEXT UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS index_versions (
                index_id TEXT PRIMARY KEY,
                created_at TEXT,
                embedding_model TEXT,
                chunk_words INTEGER,
                chunk_overlap INTEGER,
                corpus_fingerprint TEXT,
                index_path TEXT,
                shard_path TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS index_shards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shard_name TEXT UNIQUE,
                path TEXT,
                pipeline_run_id INTEGER,
                index_id TEXT,
                created_at TEXT,
                is_active INTEGER DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vectors (
                id INTEGER PRIMARY KEY,
                doc_id TEXT,
                chunk_id TEXT UNIQUE,
                chunk_hash TEXT,
                paper_path TEXT,
                page INTEGER,
                start_word INTEGER,
                end_word INTEGER,
                text TEXT,
                embedding BLOB,
                pipeline_run_id INTEGER,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS request_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                component TEXT,
                error TEXT,
                context_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_openalex_metadata (
                paper_path TEXT PRIMARY KEY,
                title TEXT,
                authors TEXT,
                query_title TEXT,
                query_authors TEXT,
                query_year INTEGER,
                openalex_id TEXT,
                openalex_doi TEXT,
                openalex_title TEXT,
                openalex_publication_year INTEGER,
                openalex_authors_json TEXT DEFAULT '[]',
                openalex_json TEXT DEFAULT '{}',
                match_status TEXT,
                error_text TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS async_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE,
                queue_name TEXT,
                job_type TEXT,
                status TEXT,
                payload_json TEXT DEFAULT '{}',
                result_json TEXT DEFAULT '{}',
                error_text TEXT,
                attempt_count INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                retry_delay_seconds INTEGER DEFAULT 10,
                available_at TEXT DEFAULT CURRENT_TIMESTAMP,
                locked_at TEXT,
                worker_id TEXT,
                started_at TEXT,
                finished_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS streamlit_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_login_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS streamlit_users_username_ci_idx
            ON streamlit_users (username COLLATE NOCASE)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS streamlit_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL UNIQUE,
                user_id INTEGER,
                username TEXT NOT NULL,
                source TEXT DEFAULT 'streamlit_ui',
                authenticated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                revoked_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._conn.commit()

    def cursor(self):
        return SQLiteCursorWrapper(self._conn.cursor())

    def commit(self):
        return self._conn.commit()

    def close(self):
        # keep underlying in-memory DB open for the test process
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


_SINGLE_CONN = None


def fake_connect(*args, **kwargs):
    global _SINGLE_CONN
    if _SINGLE_CONN is None:
        _SINGLE_CONN = SQLiteConnWrapper()
    return _SINGLE_CONN


class FakeConnectionPool:
    def __init__(self, conninfo=None, min_size=1, max_size=8, kwargs=None, open=True):
        self.conninfo = conninfo
        self.min_size = min_size
        self.max_size = max_size
        self.kwargs = kwargs or {}
        self.open = open

    def connection(self):
        class _Ctx:
            def __enter__(self_inner):
                return fake_connect()

            def __exit__(self_inner, exc_type, exc, tb):
                return False

        return _Ctx()

    def close(self):
        return None


# insert fake psycopg/psycopg2 modules for deterministic sqlite-backed tests
psycopg2_mod = types.ModuleType("psycopg2")
psycopg2_mod.connect = fake_connect
sys.modules["psycopg2"] = psycopg2_mod

psycopg_mod = types.ModuleType("psycopg")
psycopg_mod.connect = fake_connect
psycopg_mod.Connection = SQLiteConnWrapper
sys.modules["psycopg"] = psycopg_mod

psycopg_pool_mod = types.ModuleType("psycopg_pool")
psycopg_pool_mod.ConnectionPool = FakeConnectionPool
sys.modules["psycopg_pool"] = psycopg_pool_mod


# minimal fake rank_bm25 module
if "rank_bm25" not in sys.modules:
    mod = types.ModuleType("rank_bm25")

    class BM25Okapi:
        def __init__(self, tokenized_corpus):
            self.corpus = tokenized_corpus

        def get_scores(self, tokenized_query):
            scores = []
            qset = set(tokenized_query)
            for doc in self.corpus:
                # simple overlap count
                scores.append(sum(1 for t in doc if t in qset))
            return scores

    mod.BM25Okapi = BM25Okapi
    sys.modules["rank_bm25"] = mod


# fake faiss if missing
if "faiss" not in sys.modules:
    import numpy as _np

    faiss_mod = types.ModuleType("faiss")

    class IndexFlatIP:
        def __init__(self, d):
            self.d = d
            self.vectors = _np.zeros((0, d), dtype=_np.float32)

        def add(self, X):
            self.vectors = _np.vstack([self.vectors, _np.array(X, dtype=_np.float32)])

        @property
        def ntotal(self):
            return self.vectors.shape[0]

        def search(self, q, k):
            # q: (n, d)
            q = _np.array(q, dtype=_np.float32)
            # normalize
            qn = q / ( _np.linalg.norm(q, axis=1, keepdims=True) + 1e-9)
            vn = self.vectors / (_np.linalg.norm(self.vectors, axis=1, keepdims=True) + 1e-9)
            sims = _np.dot(qn, vn.T)
            idx = _np.argsort(-sims, axis=1)[:, :k]
            dist = _np.take_along_axis(sims, idx, axis=1)
            return dist, idx

    def write_index(idx, path):
        _np.savez(path, vectors=idx.vectors, d=idx.d)

    def read_index(path):
        data = _np.load(path + '.npz' if not str(path).endswith('.npz') else path)
        idx = IndexFlatIP(int(data['d']))
        idx.vectors = data['vectors'].astype(_np.float32)
        return idx

    faiss_mod.IndexFlatIP = IndexFlatIP
    faiss_mod.write_index = write_index
    faiss_mod.read_index = read_index
    sys.modules['faiss'] = faiss_mod


# fake openai if missing
if "openai" not in sys.modules:
    mod = types.ModuleType("openai")

    class FakeOpenAI:
        class embeddings:
            @staticmethod
            def create(model, input):
                class Item:
                    def __init__(self, embedding):
                        self.embedding = embedding

                # simple deterministic small vector
                return types.SimpleNamespace(data=[Item([0.01] * 8)])

        class chat:
            @staticmethod
            def completions():
                return None

    mod.OpenAI = FakeOpenAI
    sys.modules['openai'] = mod
