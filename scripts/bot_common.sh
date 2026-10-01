#!/usr/bin/env bash
# Shared Linux process identity check; source from start_bot.sh / stop_bot.sh.
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
run_directory="$project_root/.run"
pid_path="$run_directory/bot.pid"
python_path="$project_root/.venv/bin/python"
mkdir -p -- "$run_directory"
# Serialize start and stop; the child must not inherit descriptor 9.
exec 9>"$run_directory/bot.lock"
flock -n 9 || { echo "Another start/stop operation is in progress." >&2; exit 1; }

read_bot_pid() {
    bot_pid="$(cat -- "$pid_path")"
    if [[ ! "$bot_pid" =~ ^[1-9][0-9]*$ ]]; then
        echo "Invalid PID in $pid_path. No process was stopped or started." >&2
        exit 1
    fi
}

bot_is_alive() {
    [[ -r "/proc/$bot_pid/stat" ]] || return 1
    # A zombie has exited, even though kill -0 would still succeed.
    local process_stat
    process_stat="$(cat "/proc/$bot_pid/stat")"
    [[ "${process_stat##*) }" != Z\ * ]] && kill -0 "$bot_pid" 2>/dev/null
}

verify_bot_identity() {
    local actual expected
    actual="$(tr '\0' '\n' <"/proc/$bot_pid/cmdline")"
    expected="$(printf '%s\n' "$python_path" -u -X utf8 -m src.main)"
    if [[ "$actual" != "$expected" ]]; then
        echo "PID $bot_pid belongs to another process. No process was stopped or started." >&2
        exit 1
    fi
}
