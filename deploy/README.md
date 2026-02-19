Deploy (Cloud Stubs) WIP
========================

This folder contains minimal infrastructure stubs for cloud deployments.
They are intentionally lightweight and meant to be adapted to your environment.

Targets
-------

- AWS: ECS/Fargate + RDS + S3
- GCP: Cloud Run or GKE + Cloud SQL + GCS

Queueing Model
--------------

- Async workflow and indexing jobs are Postgres-backed (`workflow.async_jobs`).
- Jobs are processed by the queue worker container.
- No Redis dependency is required for runtime queueing.

Layout
------

- `deploy/terraform/aws/`: Terraform stubs for AWS.
- `deploy/terraform/gcp/`: Terraform stubs for GCP.

Usage (Example)
---------------

1. Copy the files to your infra repo or extend in-place.
2. Fill in placeholders (project IDs, VPC IDs, subnets, etc.).
3. Run `terraform init`, `terraform plan`, and `terraform apply`.

Notes
-----

- These stubs do not create IAM policies, secrets, or networking by default.
- Store secrets in a managed service (AWS Secrets Manager / GCP Secret Manager).
- Prefer private networking between app and Postgres.

Architecture Docs
-----------------

- [System architecture](../docs/architecture/architecture.md)
- [Workflow architecture](../docs/architecture/workflow_architecture.md)
- [Postgres ERD](../docs/architecture/data-model-erd.md)
