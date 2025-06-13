# AWS Deployment Guide

## Prerequisites
1. AWS Account
2. AWS CLI installed and configured
3. Docker installed locally
4. ECR (Elastic Container Registry) repository created

## Deployment Steps

### 1. Build and Push Docker Image
```bash
# Login to ECR
aws ecr get-login-password --region <your-region> | docker login --username AWS --password-stdin <your-account-id>.dkr.ecr.<your-region>.amazonaws.com

# Build the image
docker build -t nf-cookies-checker .

# Tag the image
docker tag nf-cookies-checker:latest <your-account-id>.dkr.ecr.<your-region>.amazonaws.com/nf-cookies-checker:latest

# Push to ECR
docker push <your-account-id>.dkr.ecr.<your-region>.amazonaws.com/nf-cookies-checker:latest
```

### 2. AWS ECS Setup
1. Create an ECS cluster
2. Create a task definition with the following:
   - Container image: Your ECR image
   - Memory: 512MB (adjust as needed)
   - CPU: 256 units (adjust as needed)
   - Environment variables:
     - TELEGRAM_BOT_TOKEN
     - TELEGRAM_CHAT_ID
     - Other required environment variables

### 3. Security Considerations
1. Create an IAM role for ECS task execution
2. Set up VPC and security groups
3. Configure environment variables in AWS Systems Manager Parameter Store

### 4. Monitoring Setup
1. Set up CloudWatch Logs
2. Configure CloudWatch Alarms for monitoring
3. Set up CloudWatch Metrics for performance tracking

## Environment Variables
Make sure to set these environment variables in your ECS task definition:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- Any other configuration variables from config.py

## Maintenance
1. Regularly update the Docker image
2. Monitor CloudWatch logs
3. Set up auto-scaling if needed
4. Regular security updates

## Troubleshooting
1. Check CloudWatch logs for errors
2. Verify environment variables
3. Check ECS task status
4. Verify network connectivity 