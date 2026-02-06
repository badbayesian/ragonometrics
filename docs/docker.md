# Docker and Containerization

Docker Compose
--------------
Build and start the UI + services:

```bash
docker compose up --build streamlit redis postgres
```

Run the agentic workflow in a container:

```bash
docker compose run --rm workflow
```

Notes:
- The compose file runs code from the image (no repo bind mount). This avoids Windows/network drive mount issues.
- If you want live code edits, add a bind mount like `- ./:/app:rw` to the service and ensure Docker Desktop can access the drive.
- Workflow reports are written to `reports/` (bind mounted in the `workflow` service).
