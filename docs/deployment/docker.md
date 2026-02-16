# Docker and Containerization

Docker Compose
--------------
Build and start the UI + services:

```bash
docker compose up --build streamlit postgres rq-worker
```

Run the agentic workflow in a container (defaults to the command in `compose.yml`):

```bash
docker compose run --rm workflow
```

Run the workflow with all report questions:

```bash
docker compose run --rm workflow ragonometrics workflow --papers /app/papers --agentic --agentic-citations --report-question-set both
```

Run the prep phase only:

```bash
docker compose run --rm -e PREP_VALIDATE_ONLY=1 workflow
```

Stand up pgAdmin and auto-load the Ragonometrics Postgres server:

```bash
python tools/standup_pgadmin.py
```

Open the URL printed by the script (default `http://localhost:5050`) after startup.

Notes:
- The compose file ([`compose.yml`](https://github.com/badbayesian/ragonometrics/blob/main/compose.yml)) runs code from the image (no repo bind mount). This avoids Windows/network drive mount issues.
- The `postgres` service uses a Timescale image so `vectorscale` is available; Ragonometrics enables extensions during metadata DB initialization.
- The `pgadmin` service is available for table inspection and is preloaded with a server entry pointing at the compose `postgres` service.
- If you want live code edits, add a bind mount like `- ./:/app:rw` to the service and ensure Docker Desktop can access the drive.
- Workflow reports are written to [`reports/`](https://github.com/badbayesian/ragonometrics/tree/main/reports) (bind mounted in the `workflow` service).
- The compose services load environment variables from `.env` via `env_file`, so keep API keys and workflow settings there.
- OpenAlex enrichment uses `OPENALEX_API_KEY` and optional `OPENALEX_MAILTO` from `.env`.
