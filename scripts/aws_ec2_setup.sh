#!/bin/bash
# aws_ec2_setup.sh — run ONCE on a fresh Ubuntu 22.04 EC2 instance.
#
# Prerequisites (do these in the AWS console/CLI first — see the deployment
# guide for the full walkthrough):
#   1. RDS PostgreSQL instance created, endpoint + credentials in hand.
#   2. ElastiCache Redis cluster created, primary endpoint in hand.
#   3. EC2 instance launched (Ubuntu 22.04, t3.small or larger), in the SAME
#      VPC as the RDS/ElastiCache instances (or peered/reachable).
#   4. Security group on RDS/ElastiCache allows inbound from the EC2
#      instance's security group on ports 5432 / 6379.
#   5. EC2 security group allows inbound 22 (SSH), 80, 443 (HTTP/S) from the
#      internet, and an Elastic IP is associated so the address is stable.
#
# Usage: scp this file to the instance, then:
#   ssh ubuntu@<EC2_IP>
#   chmod +x aws_ec2_setup.sh && sudo ./aws_ec2_setup.sh

set -e

echo "=== Updating packages ==="
apt-get update -y

echo "=== Installing Docker ==="
curl -fsSL https://get.docker.com | sh
systemctl enable docker
usermod -aG docker ubuntu || true

echo "=== Installing Docker Compose plugin ==="
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

echo "=== Setting up firewall (ufw) ==="
ufw allow 22/tcp      # SSH
ufw allow 80/tcp      # HTTP
ufw allow 443/tcp     # HTTPS
ufw --force enable

echo "=== Cloning project ==="
mkdir -p /opt/lifodial
git clone https://github.com/ainan-codes/LIfoDial.git /opt/lifodial
cd /opt/lifodial

echo "=== Next steps ==="
echo "1. Copy your .env (based on .env.aws.example) to /opt/lifodial/.env:"
echo "     scp .env ubuntu@<EC2_IP>:/opt/lifodial/.env"
echo "2. Edit nginx.aws.conf and set server_name to your real API domain."
echo "3. Run: cd /opt/lifodial && ./scripts/deploy_aws.sh initial"
echo "4. Point your domain's DNS A record at this instance's Elastic IP."
echo "5. Run: ./scripts/deploy_aws.sh ssl api.yourdomain.com you@example.com"
