#!/usr/bin/env bash
set -Eeuo pipefail

# Runs the WebUI miner natively under systemd (no Docker), with hard CPU and
# memory limits so it can never take the whole server down.
# The first run migrates from the old Docker/pm2 setup; later runs only update.
# Each checkout is its own instance (one Twitch account): its own config/,
# cookies, cache, systemd service and update timer. See docs/server.md.
#
#   ./update-server.sh          update and restart now (also retries a commit that failed before)
#   ./update-server.sh --auto   what the timer runs: does nothing unless origin moved
#                               to a commit that has not already failed here
#
# Optional overrides: CPU_QUOTA=50% MEMORY_MAX=1G HEALTH_TIMEOUT=120
#                     AUTO_UPDATE=0 (removes the update timer) ./update-server.sh

# Execute a copy so git can safely update the script while it is running.
if [[ ${1:-} != --copied ]]; then
    project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    script_copy=$(mktemp)
    cp -- "${BASH_SOURCE[0]}" "$script_copy"
    exec bash "$script_copy" --copied "$project_dir" "$script_copy" "$@"
fi

project_dir=$2
script_copy=$3
shift 3
trap 'rm -f -- "$script_copy"' EXIT
cd -- "$project_dir"

auto=0
for arg in "$@"; do
    case "$arg" in
        --auto) auto=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

branch=webui
service="twitch-drops-$(basename -- "$project_dir" | tr -c 'A-Za-z0-9_.\n-' '-')"
unit=/etc/systemd/system/$service.service
timer_service=/etc/systemd/system/$service-update.service
timer_unit=/etc/systemd/system/$service-update.timer
venv=$project_dir/env
env_file=$project_dir/config/server.env
cpu_quota=${CPU_QUOTA:-50%}
memory_max=${MEMORY_MAX:-1G}
health_timeout=${HEALTH_TIMEOUT:-120}
old_container=twitch-drops-miner
pm2_name=
legacy_instance=0
case "$project_dir" in
    /home/ubuntu/TwitchDropsMiner) pm2_name=aurum-twitch-drop; legacy_instance=1 ;;
    /home/ubuntu/vaidas_twitch_drops) pm2_name=vaidas-twitch-drop ;;
esac

log() { echo "[$(date '+%F %T')] $*"; }

for command in git python3 sudo systemctl curl flock; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
if (( auto )) && ! sudo -n true 2>/dev/null; then
    echo 'Automatic updates need passwordless sudo for this user.' >&2; exit 1
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || {
    echo 'Python 3.10 or newer is required.' >&2; exit 1;
}
[[ $(git branch --show-current) == "$branch" ]] || {
    echo "Switch to branch $branch before updating." >&2; exit 1;
}
git diff --quiet && git diff --cached --quiet || {
    echo 'Tracked files have local changes. Resolve them before updating.' >&2; exit 1;
}
bad_commit_file=$(git rev-parse --git-path tdm-bad-commit)

# One update per instance at a time: the timer and a manual run can overlap.
exec 9>"$(git rev-parse --git-path tdm-update.lock)"
if ! flock -n 9; then
    log 'Another update of this instance is running; skipping.'
    exit 0
fi

git fetch --quiet origin "$branch"
previous=$(git rev-parse HEAD)
target=$(git rev-parse "origin/$branch")
if (( auto )); then
    [[ $previous != "$target" ]] || exit 0
    if [[ $target == "$(cat "$bad_commit_file" 2>/dev/null)" ]]; then
        log "Skipping ${target:0:7}: it failed to start here before. Run ./update-server.sh to retry it."
        exit 0
    fi
fi

if [[ ! -f $unit ]]; then
    log "First run: setting up systemd service $service..."
    # The old Docker container belonged to the original checkout only.
    if (( legacy_instance )) && command -v docker >/dev/null \
        && docker inspect "$old_container" >/dev/null 2>&1; then
        # Keep the container's own settings, not the image's paths and versions.
        if [[ ! -f $env_file ]]; then
            sudo mkdir -p -- "$project_dir/config"
            docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$old_container" \
                | grep -vE '^$|^(PATH|HOME|HOSTNAME|LANG|GPG_KEY|PYTHON_[A-Z0-9_]*|WEBUI_BROWSER_PATH|WEBUI_NOVNC_PATH)=' \
                | sudo tee -- "$env_file" >/dev/null || true
            echo "Saved the container's settings to $env_file"
        fi
        # Kept, but stopped and never restarted, so it can still be inspected.
        docker update --restart=no "$old_container" >/dev/null
        docker stop "$old_container" >/dev/null || true
        echo "Stopped Docker container $old_container (kept; remove it with: docker rm $old_container)"
    fi
    if [[ -n $pm2_name ]] && command -v pm2 >/dev/null && pm2 describe "$pm2_name" >/dev/null 2>&1; then
        pm2 delete "$pm2_name"
        pm2 save
    fi
    # Docker ran as root, so files it wrote into config/ belong to root.
    sudo chown -R "$(id -u):$(id -g)" -- "$project_dir/config" 2>/dev/null || true
    # Xvfb, x11vnc and noVNC are only used by the WebUI login window.
    sudo apt-get update -qq
    sudo apt-get install -y -qq --no-install-recommends \
        python3-venv xvfb x11vnc novnc fonts-dejavu-core
fi

# Every instance needs its own WebUI port.
port=$(sed -n 's/^WEBUI_PORT=//p' "$env_file" 2>/dev/null | tail -n 1 || true)
port=${port:-5800}
for other in /etc/systemd/system/twitch-drops-*.service; do
    [[ -f $other && $other != "$unit" && $other != *-update.service ]] || continue
    other_dir=$(sed -n 's/^WorkingDirectory=//p' "$other")
    other_port=$(sed -n 's/^WEBUI_PORT=//p' "$other_dir/config/server.env" 2>/dev/null | tail -n 1 || true)
    if [[ ${other_port:-5800} == "$port" ]]; then
        echo "Port $port is already used by $other_dir. Set another WEBUI_PORT in $env_file." >&2
        exit 1
    fi
done

# Reinstall packages only when the requirements change, at the lowest priority.
install_packages() {
    [[ -x $venv/bin/python ]] || python3 -m venv "$venv"
    local requirements_hash
    requirements_hash=$(cat requirements.txt requirements-nicegui.txt | sha256sum)
    if [[ $(cat "$venv/.requirements-hash" 2>/dev/null) != "$requirements_hash" ]]; then
        log 'Installing Python packages...'
        nice -n 19 "$venv/bin/python" -m pip install -q --disable-pip-version-check \
            -r requirements-nicegui.txt
        echo "$requirements_hash" > "$venv/.requirements-hash"
    fi
}

units_changed=0
write_unit() {
    if ! printf '%s' "$2" | sudo cmp -s - "$1"; then
        printf '%s' "$2" | sudo tee "$1" >/dev/null
        units_changed=1
    fi
}

# /health answers 200 only once the app is up and its state machine runs.
wait_healthy() {
    local deadline=$((SECONDS + health_timeout))
    while (( SECONDS < deadline )); do
        if curl -fks -o /dev/null "http://127.0.0.1:$port/health" \
            || curl -fks -o /dev/null "https://127.0.0.1:$port/health"; then
            return 0
        fi
        sleep 3
    done
    return 1
}

restart_and_check() {
    sudo systemctl reset-failed "$service" 2>/dev/null || true
    sudo systemctl restart "$service"
    log "Waiting up to ${health_timeout}s for the WebUI on port $port..."
    wait_healthy
}

if [[ $previous != "$target" ]]; then
    log "Updating ${previous:0:7} -> ${target:0:7}..."
    git merge --ff-only --quiet "origin/$branch"
fi
install_packages

if ! grep -q '^WEBUI_BROWSER_PATH=' "$env_file" 2>/dev/null \
    && ! command -v google-chrome google-chrome-stable chromium chromium-browser >/dev/null; then
    echo 'Warning: no Chromium/Chrome found; Twitch login from the WebUI will not work.' >&2
    echo '         Install one with: sudo snap install chromium' >&2
fi

write_unit "$unit" "[Unit]
Description=Twitch Drops Miner ($project_dir)
After=network-online.target
Wants=network-online.target
# Give up after 5 crashes in 15 minutes instead of restarting forever.
StartLimitIntervalSec=900
StartLimitBurst=5

[Service]
User=$(id -un)
WorkingDirectory=$project_dir
EnvironmentFile=-$env_file
Environment=PYTHONUNBUFFERED=1
ExecStart=$venv/bin/python main_webui.py
Restart=on-failure
RestartSec=30
TimeoutStopSec=20
KillMode=control-group
Nice=10
CPUQuota=$cpu_quota
MemoryMax=$memory_max
TasksMax=512

[Install]
WantedBy=multi-user.target
"
if [[ ${AUTO_UPDATE:-1} == 1 ]]; then
    write_unit "$timer_service" "[Unit]
Description=Update Twitch Drops Miner ($project_dir)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$(id -un)
ExecStart=/bin/bash $project_dir/update-server.sh --auto
Nice=10
"
    write_unit "$timer_unit" "[Unit]
Description=Check for Twitch Drops Miner updates ($project_dir)

[Timer]
OnBootSec=10min
OnUnitActiveSec=30min
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
"
elif [[ -f $timer_unit ]]; then
    sudo systemctl disable --now --quiet "$service-update.timer" || true
    sudo rm -f -- "$timer_unit" "$timer_service"
    units_changed=1
fi
if (( units_changed )); then
    sudo systemctl daemon-reload
fi
sudo systemctl enable --quiet "$service"
if [[ -f $timer_unit ]]; then
    sudo systemctl enable --now --quiet "$service-update.timer"
fi

if restart_and_check; then
    rm -f -- "$bad_commit_file"
    log "Running $(git rev-parse --short HEAD). Logs: journalctl -u $service -f"
    systemctl --no-pager status "$service" | head -n 12 || true
    exit 0
fi

echo 'The miner did not start. Recent logs:' >&2
sudo journalctl -u "$service" -n 40 --no-pager >&2 || true
current=$(git rev-parse HEAD)
[[ $current != "$previous" ]] || exit 1

# Go back to the commit that ran before, and remember the bad one so the
# timer does not redeploy it every half hour.
echo "$current" > "$bad_commit_file"
log "Rolling back to ${previous:0:7}..."
git reset --hard --quiet "$previous"
install_packages
if restart_and_check; then
    log "Rolled back to ${previous:0:7}. ${current:0:7} is skipped by the timer until a manual run."
else
    log "The rollback to ${previous:0:7} did not start either. Check: journalctl -u $service"
fi
exit 1
