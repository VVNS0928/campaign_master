#!/usr/bin/env bash
# UNTESTED: written without AWS access. Read it, run with --dry-run first.
# Launches a t4g.micro that runs the whole e2e suite against itself, prints the result, and terminates itself.
# No inbound ports, no SSH, no S3. Cost is a few minutes of t4g.micro (fractions of a cent) + a tiny EBS volume.
# Needs: aws cli configured, REPO_URL of a PUBLIC git repo containing this project.
set -euo pipefail
: "${REPO_URL:?set REPO_URL=https://github.com/you/gradial-contracts.git}"
REGION="${AWS_REGION:-us-east-1}"
DRY=""; [[ "${1:-}" == "--dry-run" ]] && DRY="--dry-run"

AMI=$(aws ssm get-parameter --region "$REGION" \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 --query Parameter.Value --output text)

USERDATA=$(cat <<UD
#!/bin/bash
exec > >(tee /var/log/e2e.log | logger -t e2e -s 2>/dev/console) 2>&1
dnf install -y git python3.12 python3.12-pip >/dev/null
git clone --depth 1 "$REPO_URL" /app && cd /app
python3.12 -m pip install -q ".[service,e2e]"
python3.12 -m pytest -q
E2E_HOOKS=1 API_KEY=aws-key nohup python3.12 -m uvicorn gradial_contracts.service.app:app --port 8000 >/tmp/srv.log 2>&1 &
for i in \$(seq 1 30); do curl -sf localhost:8000/health && break; sleep 1; done
E2E_DESTRUCTIVE=1 API_KEY=aws-key BASE_URL=http://127.0.0.1:8000 python3.12 -m pytest e2e -v
echo "E2E_EXIT_CODE=\$?"
shutdown -h now
UD
)

ID=$(aws ec2 run-instances $DRY --region "$REGION" --image-id "$AMI" --instance-type t4g.micro \
  --instance-initiated-shutdown-behavior terminate \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=8,VolumeType=gp3,DeleteOnTermination=true}' \
  --metadata-options HttpTokens=required \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=gradial-e2e-oneshot}]' \
  --user-data "$USERDATA" --query 'Instances[0].InstanceId' --output text) || { echo "dry-run ok or launch failed"; exit 0; }
echo "Launched $ID. Waiting for it to finish (about 3-5 min)..."
aws ec2 wait instance-terminated --region "$REGION" --instance-ids "$ID" || true
echo "---- console output (look for E2E_EXIT_CODE=0) ----"
aws ec2 get-console-output --region "$REGION" --instance-id "$ID" --latest --output text | tail -60
