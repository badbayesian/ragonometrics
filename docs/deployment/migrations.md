# Migrations and Backfill

This repo uses Alembic for Postgres schema ownership.

## Apply migrations

Docker:

```bash
docker compose run --rm migrate
```

Local:

```bash
ragonometrics db migrate --db-url "$DATABASE_URL"
```

## Backfill historical data

Backfill legacy sqlite/query/report artifacts into Postgres:

```bash
python tools/backfill_sqlite_to_postgres.py --db-url "$DATABASE_URL"
```

Validate row-count parity:

```bash
python tools/validate_backfill_parity.py --db-url "$DATABASE_URL"
```

Backfill full structured-question fields for older compact Streamlit rows:

```bash
python tools/backfill_structured_question_fields.py --db-url "$DATABASE_URL" --apply
```

## Notes

- `deploy/sql/*.sql` remain human-readable SQL references used by migrations.
- Runtime modules fail fast if migrations are missing/outdated.
- Expected schema revision is enforced in `ragonometrics/db/connection.py`.
- Latest additive migration: `0008_web_chat_history` creates `retrieval.chat_history_turns` for server-side web chat persistence.
