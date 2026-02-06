variable "aws_region" {
  type        = string
  description = "AWS region for deployment"
  default     = "us-east-1"
}

variable "project_name" {
  type        = string
  description = "Project name prefix"
  default     = "ragonometrics"
}

variable "vpc_id" {
  type        = string
  description = "Existing VPC ID"
}

variable "artifact_bucket_name" {
  type        = string
  description = "S3 bucket for artifacts"
}

variable "db_instance_id" {
  type        = string
  description = "RDS instance identifier"
  default     = "ragonometrics-db"
}

variable "db_engine_version" {
  type        = string
  description = "Postgres engine version"
  default     = "16.2"
}

variable "db_instance_class" {
  type        = string
  description = "RDS instance class"
  default     = "db.t3.medium"
}

variable "db_allocated_gb" {
  type        = number
  description = "Allocated storage (GB)"
  default     = 50
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

variable "db_security_group_ids" {
  type        = list(string)
  description = "Security groups for RDS"
  default     = []
}

variable "db_subnet_group" {
  type        = string
  description = "RDS subnet group name"
}

variable "redis_node_type" {
  type        = string
  description = "Redis node type"
  default     = "cache.t3.micro"
}

variable "redis_security_group_ids" {
  type        = list(string)
  description = "Security groups for Redis"
  default     = []
}

variable "tags" {
  type        = map(string)
  description = "Common tags"
  default     = {}
}
