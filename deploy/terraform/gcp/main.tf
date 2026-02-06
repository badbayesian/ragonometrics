terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_storage_bucket" "artifacts" {
  name     = var.artifact_bucket_name
  location = var.region
}

resource "google_sql_database_instance" "postgres" {
  name             = var.db_instance_id
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier = var.db_tier
    backup_configuration {
      enabled = true
    }
  }
}

resource "google_sql_user" "db_user" {
  name     = var.db_username
  instance = google_sql_database_instance.postgres.name
  password = var.db_password
}

resource "google_sql_database" "db" {
  name     = var.db_name
  instance = google_sql_database_instance.postgres.name
}

resource "google_redis_instance" "redis" {
  name           = "${var.project_name}-redis"
  tier           = "BASIC"
  memory_size_gb = var.redis_memory_gb
  region         = var.region
}

output "postgres_connection_name" {
  value = google_sql_database_instance.postgres.connection_name
}

output "redis_host" {
  value = google_redis_instance.redis.host
}

output "artifact_bucket" {
  value = google_storage_bucket.artifacts.name
}
