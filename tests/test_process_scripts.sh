#!/usr/bin/env bash
# Isolated lifecycle smoke test: no VK credentials, API calls, or project data.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
fixture="$(mktemp -d)/project with spaces"
mkdir -p "$fixture/scripts" "$fixture/src" "$fixture/.venv/bin" "$fixture/tools"
cleanup() {
    if [[ -f "$fixture/.run/bot.pid" ]]; then
        bash "$fixture/scripts/stop_bot.sh" >/dev/null 2>&1 || true
    fi
    rm -rf -- "${fixture%/project with spaces}"
}
trap cleanup EXIT
cp "$repo_root"/scripts/*.sh "$fixture/scripts/"
ln -s "$(command -v python3)" "$fixture/.venv/bin/python"
cat >"$fixture/tools/uv" <<'UV'
#!/usr/bin/env bash
set -euo pipefail
[[ "$*" == 'sync --frozen' ]]
[[ "$PWD" == "$SCRIPT_TEST_ROOT" ]]
UV
chmod +x "$fixture/tools/uv"
export PATH="$fixture/tools:$PATH"
export SCRIPT_TEST_ROOT="$fixture"
cat >"$fixture/src/main.py" <<'PY'
import sys
import time
print("isolated bot ready", flush=True)
print("isolated stderr", file=sys.stderr, flush=True)
while True:
    time.sleep(1)
PY

# Run from a different directory, with a project path containing spaces.
cd /
bash "$fixture/scripts/stop_bot.sh"
bash "$fixture/scripts/start_bot.sh"
saved_pid="$(cat "$fixture/.run/bot.pid")"
kill -0 "$saved_pid"
bash "$fixture/scripts/start_bot.sh"
[[ "$(cat "$fixture/.run/bot.pid")" == "$saved_pid" ]]
grep -q 'isolated bot ready' "$fixture/logs/bot.stdout.log"
grep -q 'isolated stderr' "$fixture/logs/bot.stderr.log"
bash "$fixture/scripts/stop_bot.sh"
[[ ! -f "$fixture/.run/bot.pid" ]]
bash "$fixture/scripts/stop_bot.sh"
[[ -s "$fixture/logs/bot.stdout.log" ]]

# A stale PID is cleaned; an unrelated process or malformed PID is refused.
printf '%s\n' 2147483647 >"$fixture/.run/bot.pid"
bash "$fixture/scripts/stop_bot.sh"
[[ ! -f "$fixture/.run/bot.pid" ]]
printf '%s\n' "$$" >"$fixture/.run/bot.pid"
if bash "$fixture/scripts/stop_bot.sh"; then exit 1; fi
kill -0 "$$"
if bash "$fixture/scripts/start_bot.sh"; then exit 1; fi
printf '%s\n' invalid >"$fixture/.run/bot.pid"
if bash "$fixture/scripts/stop_bot.sh"; then exit 1; fi
rm -- "$fixture/.run/bot.pid"

# A bot that crashes at startup must not leave a misleading PID file.
printf '%s\n' 'raise RuntimeError("isolated startup failure")' >"$fixture/src/main.py"
if bash "$fixture/scripts/start_bot.sh"; then exit 1; fi
[[ ! -f "$fixture/.run/bot.pid" ]]
echo "Linux lifecycle smoke test: OK"
