variable "project_id" {
  type        = string
  description = "GCP project id"
}

variable "region" {
  type        = string
  description = "GCP region"
  default     = "us-central1"
}

variable "project_name" {
  type        = string
  description = "Project name prefix"
  default     = "ragonometrics"
}

variable "artifact_bucket_name" {
  type        = string
  description = "GCS bucket for artifacts"
}

variable "db_instance_id" {
  type        = string
  description = "Cloud SQL instance id"
  default     = "ragonometrics-db"
}

variable "db_tier" {
  type        = string
  description = "Cloud SQL machine tier"
  default     = "db-custom-2-7680"
}

variable "db_name" {
  type        = string
  description = "Database name"
  default     = "ragonometrics"
}

variable "db_username" {
  type        = string
  description = "Database username"
}

variable "db_password" {
  type        = string
  description = "Database password"
  sensitive   = true
}

variable "redis_memory_gb" {
  type        = number
  description = "Redis instance memory"
  default     = 1
}
