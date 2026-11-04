#!/usr/bin/env bash
set -Eeuo pipefail

# Runs the WebUI miner natively under systemd (no Docker), with hard CPU and
# memory limits so it can never take the whole server down.
# The first run migrates from the old Docker/pm2 setup; later runs only update.
# Optional overrides: CPU_QUOTA=50% MEMORY_MAX=1G ./update-server.sh

# Execute a copy so git can safely update the script while it is running.
if [[ ${1:-} != --copied ]]; then
    project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    script_copy=$(mktemp)
    cp -- "${BASH_SOURCE[0]}" "$script_copy"
    exec bash "$script_copy" --copied "$project_dir" "$script_copy"
fi

project_dir=$2
script_copy=$3
trap 'rm -f -- "$script_copy"' EXIT
cd -- "$project_dir"

branch=webui
service="twitch-drops-$(basename -- "$project_dir" | tr -c 'A-Za-z0-9_.\n-' '-')"
unit=/etc/systemd/system/$service.service
venv=$project_dir/env
env_file=$project_dir/config/server.env
cpu_quota=${CPU_QUOTA:-50%}
memory_max=${MEMORY_MAX:-1G}
old_container=twitch-drops-miner
pm2_name=
case "$project_dir" in
    /home/ubuntu/TwitchDropsMiner) pm2_name=aurum-twitch-drop ;;
    /home/ubuntu/vaidas_twitch_drops) pm2_name=vaidas-twitch-drop ;;
esac

for command in git python3 sudo systemctl curl; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || {
    echo 'Python 3.10 or newer is required.' >&2; exit 1;
}
[[ $(git branch --show-current) == "$branch" ]] || {
    echo "Switch to branch $branch before updating." >&2; exit 1;
}
git diff --quiet && git diff --cached --quiet || {
    echo 'Tracked files have local changes. Resolve them before updating.' >&2; exit 1;
}

if [[ ! -f $unit ]]; then
    echo "First run: replacing the Docker/pm2 setup with systemd service $service..."
    if command -v docker >/dev/null && docker inspect "$old_container" >/dev/null 2>&1; then
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

echo 'Updating source...'
git pull --ff-only origin "$branch"

# Reinstall packages only when the requirements change, at the lowest priority.
[[ -x $venv/bin/python ]] || python3 -m venv "$venv"
requirements_hash=$(cat requirements.txt requirements-nicegui.txt | sha256sum)
if [[ $(cat "$venv/.requirements-hash" 2>/dev/null) != "$requirements_hash" ]]; then
    echo 'Installing Python packages...'
    nice -n 19 "$venv/bin/python" -m pip install -q --disable-pip-version-check \
        -r requirements-nicegui.txt
    echo "$requirements_hash" > "$venv/.requirements-hash"
fi

if ! grep -q '^WEBUI_BROWSER_PATH=' "$env_file" 2>/dev/null \
    && ! command -v google-chrome google-chrome-stable chromium chromium-browser >/dev/null; then
    echo 'Warning: no Chromium/Chrome found; Twitch login from the WebUI will not work.' >&2
    echo '         Install one with: sudo snap install chromium' >&2
fi

unit_text="[Unit]
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
if ! printf '%s' "$unit_text" | sudo cmp -s - "$unit"; then
    printf '%s' "$unit_text" | sudo tee "$unit" >/dev/null
    sudo systemctl daemon-reload
fi
sudo systemctl enable --quiet "$service"
sudo systemctl reset-failed "$service" 2>/dev/null || true
sudo systemctl restart "$service"

port=$(sed -n 's/^WEBUI_PORT=//p' "$env_file" 2>/dev/null | tail -n 1 || true)
echo "Waiting for the WebUI on port ${port:-5800}..."
for _ in {1..30}; do
    if curl -ks -o /dev/null "http://127.0.0.1:${port:-5800}/health" \
        || curl -ks -o /dev/null "https://127.0.0.1:${port:-5800}/health"; then
        echo "Update complete. Logs: journalctl -u $service -f"
        systemctl --no-pager status "$service" | head -n 12 || true
        exit 0
    fi
    systemctl is-active --quiet "$service" || break
    sleep 2
done
echo 'The miner did not start. Recent logs:' >&2
sudo journalctl -u "$service" -n 40 --no-pager >&2
exit 1
