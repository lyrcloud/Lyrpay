#!/usr/bin/env bash
set -e

if [ -z "$S3_BUCKET" ]; then
  echo "ERROR: S3_BUCKET must be set."
  echo "Usage: S3_BUCKET=my-bucket ./deploy-lambda.sh [STACK_NAME]"
  exit 1
fi

STACK_NAME=${1:-lyrpay-backend-stack}
STAGE_NAME=${STAGE_NAME:-prod}

echo "Building Lambda package with SAM..."
sam build --template-file template.yaml --use-container

echo "Packaging CloudFormation template to S3 bucket: $S3_BUCKET"
sam package \
  --template-file .aws-sam/build/template.yaml \
  --s3-bucket "$S3_BUCKET" \
  --output-template-file packaged.yaml

echo "Deploying CloudFormation stack: $STACK_NAME"
sam deploy \
  --template-file packaged.yaml \
  --stack-name "$STACK_NAME" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides StageName="$STAGE_NAME"

echo "Deployment complete."
