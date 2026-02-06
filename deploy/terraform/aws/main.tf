terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_vpc" "selected" {
  id = var.vpc_id
}

data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [var.vpc_id]
  }
  tags = {
    Tier = "private"
  }
}

resource "aws_s3_bucket" "artifacts" {
  bucket = var.artifact_bucket_name
  tags   = var.tags
}

resource "aws_db_instance" "postgres" {
  identifier             = var.db_instance_id
  engine                 = "postgres"
  engine_version         = var.db_engine_version
  instance_class         = var.db_instance_class
  allocated_storage      = var.db_allocated_gb
  db_name                = var.db_name
  username               = var.db_username
  password               = var.db_password
  vpc_security_group_ids = var.db_security_group_ids
  db_subnet_group_name   = var.db_subnet_group
  skip_final_snapshot    = true
  tags                   = var.tags
}

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.project_name}-redis-subnets"
  subnet_ids = data.aws_subnets.private.ids
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${var.project_name}-redis"
  engine               = "redis"
  node_type            = var.redis_node_type
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = var.redis_security_group_ids
  tags                 = var.tags
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ragonometrics/app"
  retention_in_days = 14
  tags              = var.tags
}

output "postgres_endpoint" {
  value = aws_db_instance.postgres.address
}

output "redis_endpoint" {
  value = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}
