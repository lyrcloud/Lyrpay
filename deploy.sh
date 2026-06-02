#!/usr/bin/env bash
set -e

if [ -z "$S3_BUCKET" ] || [ -z "$LAMBDA_FUNCTION_NAME" ]; then
  echo "Please set S3_BUCKET and LAMBDA_FUNCTION_NAME environment variables."
  echo "Example: S3_BUCKET=my-bucket LAMBDA_FUNCTION_NAME=lyrpay-backend ./deploy.sh"
  exit 1
fi

echo "Building frontend..."
cd frontend
npm install
npm run build
cd ..

echo "Syncing frontend build to S3 bucket: $S3_BUCKET"
aws s3 sync frontend/dist s3://$S3_BUCKET --delete --acl public-read

echo "Packaging backend for Lambda..."
cd backend
rm -f deployment-package.zip
zip -r ../deployment-package.zip app requirements.txt pyproject.toml
cd ..

echo "Updating Lambda function: $LAMBDA_FUNCTION_NAME"
aws lambda update-function-code --function-name "$LAMBDA_FUNCTION_NAME" --zip-file fileb://deployment-package.zip

echo "Deployment complete."
