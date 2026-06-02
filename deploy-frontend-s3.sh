#!/usr/bin/env bash
set -e

FRONTEND_DIR="$(cd "$(dirname "$0")/frontend" && pwd)"
cd "$FRONTEND_DIR"

if [ -z "$S3_BUCKET" ]; then
  echo "ERROR: S3_BUCKET must be set."
  echo "Usage: S3_BUCKET=my-bucket ./deploy-frontend-s3.sh"
  exit 1
fi

if [ -z "$AWS_PROFILE" ] && [ -z "$AWS_ACCESS_KEY_ID" ]; then
  echo "WARNING: No AWS_PROFILE or AWS_ACCESS_KEY_ID detected."
  echo "Make sure AWS credentials are configured with 'aws configure' or environment variables."
fi

echo "Installing frontend dependencies..."
npm install

echo "Building frontend..."
npm run build

echo "Syncing dist/ to S3 bucket: $S3_BUCKET"
aws s3 sync dist/ s3://"$S3_BUCKET" --delete

echo "Configuring S3 static website hosting..."
aws s3 website s3://"$S3_BUCKET" --index-document index.html --error-document index.html

echo "Frontend deployed to S3 bucket: $S3_BUCKET"

echo "Use 'aws s3 website s3://$S3_BUCKET' to verify the website endpoint."

echo "If you are using CloudFront or a custom domain, configure those separately in AWS."
