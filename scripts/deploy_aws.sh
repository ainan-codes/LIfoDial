#!/bin/bash
# deploy_aws.sh — deployment commands for the AWS EC2 stack.
# Run this ON the EC2 instance, from /opt/lifodial.
# Uses docker-compose.aws.yml (backend + worker + nginx only —
# Postgres/Redis are RDS/ElastiCache, and the frontend is on Vercel).

set -e

COMMAND=$1
COMPOSE="docker-compose -f docker-compose.aws.yml"

case "$COMMAND" in
    "initial")
        echo "=== Initial deployment ==="
        $COMPOSE up -d --build
        echo "Running DB migrations against RDS..."
        $COMPOSE exec backend python -m alembic upgrade head
        ;;
    "update")
        echo "=== Updating application ==="
        git pull
        $COMPOSE up -d --build backend livekit-agent
        $COMPOSE exec backend python -m alembic upgrade head
        ;;
    "ssl")
        echo "=== Requesting SSL certificate ==="
        if [ -z "$2" ] || [ -z "$3" ]; then
            echo "Usage: ./scripts/deploy_aws.sh ssl <api.yourdomain.com> <your@email.com>"
            exit 1
        fi
        DOMAIN=$2
        EMAIL=$3
        docker run --rm -v certbot_certs:/etc/letsencrypt -v certbot_www:/var/www/certbot \
          certbot/certbot certonly --webroot --webroot-path=/var/www/certbot \
          -d "$DOMAIN" --email "$EMAIL" --agree-tos --no-eff-email
        $COMPOSE restart nginx
        ;;
    "logs")
        echo "=== Following logs ==="
        $COMPOSE logs -f
        ;;
    "status")
        $COMPOSE ps
        ;;
    *)
        echo "Usage: ./scripts/deploy_aws.sh [initial|update|ssl|logs|status]"
        exit 1
        ;;
esac
