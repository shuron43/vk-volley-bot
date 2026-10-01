#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/bot_common.sh"

if [[ -f "$pid_path" ]]; then
    read_bot_pid
    if bot_is_alive; then
        verify_bot_identity
        echo "Bot is already running. PID: $bot_pid"
        exit 0
    fi
    rm -- "$pid_path"
fi

cd -- "$project_root"
uv sync --frozen
log_directory="$project_root/logs"
mkdir -p -- "$log_directory"
nohup "$python_path" -u -X utf8 -m src.main \
    </dev/null >>"$log_directory/bot.stdout.log" \
    2>>"$log_directory/bot.stderr.log" 9>&- &
bot_pid=$!
printf '%s\n' "$bot_pid" >"$pid_path"
sleep 2
if ! bot_is_alive; then
    rm -- "$pid_path"
    echo "Bot exited during startup. Check $log_directory/bot.stderr.log" >&2
    exit 1
fi
verify_bot_identity
echo "Bot started. PID: $bot_pid"
echo "stdout: $log_directory/bot.stdout.log"
echo "stderr: $log_directory/bot.stderr.log"
