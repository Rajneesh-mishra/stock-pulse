#!/bin/bash
# Usage: ./send_telegram.sh "Your message here"
# Reads credentials from .env file — NEVER hardcode tokens

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/.env"

TOKEN="${TELEGRAM_BOT_TOKEN}"
IFS=',' read -ra CHAT_IDS <<< "${TELEGRAM_CHAT_IDS}"
MSG="$1"

for CHAT_ID in "${CHAT_IDS[@]}"; do
  curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
    -d "chat_id=${CHAT_ID}" \
    -d "text=${MSG}" \
    -d "parse_mode=Markdown" > /dev/null
done
