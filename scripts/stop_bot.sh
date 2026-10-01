#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/bot_common.sh"

if [[ ! -f "$pid_path" ]]; then
    echo "No bot PID file. The bot is not running through these scripts."
    exit 0
fi
read_bot_pid
if ! bot_is_alive; then
    rm -- "$pid_path"
    echo "Bot has already stopped. Stale PID file removed."
    exit 0
fi
verify_bot_identity
kill -TERM "$bot_pid"
for ((attempt = 0; attempt < 100; attempt++)); do
    if ! bot_is_alive; then
        rm -- "$pid_path"
        echo "Bot stopped. PID: $bot_pid. Logs were retained."
        exit 0
    fi
    sleep 0.1
done
# Recheck identity before escalation in case Linux has reused the PID.
verify_bot_identity
kill -KILL "$bot_pid"
for ((attempt = 0; attempt < 50; attempt++)); do
    if ! bot_is_alive; then
        rm -- "$pid_path"
        echo "Bot force-stopped after the timeout. PID: $bot_pid. Logs were retained."
        exit 0
    fi
    sleep 0.1
done
echo "Bot did not stop. PID file was retained." >&2
exit 1
