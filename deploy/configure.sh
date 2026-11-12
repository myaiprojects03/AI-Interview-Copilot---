#!/usr/bin/env bash
# Fill in the production settings in .env: the public addresses and strong random secrets.
# Run from the project folder:
#
#   deploy/configure.sh <domain>     on the server
#   deploy/configure.sh --local      on your computer, to try the production stack at
#                                    http://localhost:8080 (app) and http://localhost:8000 (API)
#
# The app is served at app.<domain> and the API at api.<domain>. Without a domain of your own,
# use the free sslip.io name for the server's public IP, e.g. for 140.238.1.2:
#   deploy/configure.sh 140-238-1-2.sslip.io
# Secrets that are already set are kept (changing POSTGRES_PASSWORD after the first start
# would lock the app out of its own database).
set -euo pipefail
cd "$(dirname "$0")/.."
domain=${1:?usage: deploy/configure.sh <domain> (e.g. 140-238-1-2.sslip.io) or deploy/configure.sh --local}
[ -f .env ] || cp .env.example .env

set_var() {  # replace KEY=... or append it (sed -i.bak works on both Linux and macOS)
  if grep -q "^$1=" .env; then
    sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
current() { grep "^$1=" .env | head -1 | cut -d= -f2- || true; }
new_secret() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 40; }

if [ "$domain" = "--local" ]; then
  # Empty addresses: docker-compose.prod.yml then uses localhost:8080 and localhost:8000.
  for key in APP_ADDRESS API_ADDRESS PUBLIC_APP_URL PUBLIC_API_URL; do set_var "$key" ""; done
  where="http://localhost:8080 (app) and http://localhost:8000 (API)"
else
  set_var APP_ADDRESS "app.$domain"
  set_var API_ADDRESS "api.$domain"
  set_var PUBLIC_APP_URL "https://app.$domain"
  set_var PUBLIC_API_URL "https://api.$domain"
  where="https://app.$domain and https://api.$domain"
fi
# Local Stripe test values (README) must never reach a server: their secret is public.
if [ "$domain" != "--local" ]; then
  case "$(current STRIPE_WEBHOOK_SECRET)" in whsec_local*) set_var STRIPE_WEBHOOK_SECRET "" ;; esac
  case "$(current STRIPE_PRICE_PRO)" in price_local*) set_var STRIPE_PRICE_PRO "" ;; esac
  case "$(current STRIPE_PRICE_PREMIUM)" in price_local*) set_var STRIPE_PRICE_PREMIUM "" ;; esac
fi
for key in JWT_SECRET NEXTAUTH_SECRET INTERNAL_API_SECRET POSTGRES_PASSWORD; do
  value=$(current "$key")
  set_var "$key" "${value:-$(new_secret)}"
done
chmod 600 .env
echo "Configured for $where."
echo "Next: docker compose -f docker-compose.prod.yml up -d --build"
