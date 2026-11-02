#!/usr/bin/env bash
set -Eeuo pipefail

# Execute a copy so git can safely update the script while it is running.
if [[ ${1:-} != --copied ]]; then
    project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    script_copy=$(mktemp)
    cp -- "${BASH_SOURCE[0]}" "$script_copy"
    exec bash "$script_copy" --copied "$project_dir" "$script_copy"
fi

project_dir=$2
script_copy=$3
env_file=$(mktemp)
inspect_file=$(mktemp)
trap 'rm -f -- "$env_file" "$inspect_file" "$script_copy"' EXIT
cd -- "$project_dir"

container=twitch-drops-miner
image=twitch-drops-miner:latest
build_image="twitch-drops-miner:update-$$"
branch=codex/latest-webui-sync
pm2_name=
case "$project_dir" in
    /home/ubuntu/TwitchDropsMiner) pm2_name=aurum-twitch-drop ;;
    /home/ubuntu/vaidas_twitch_drops) pm2_name=vaidas-twitch-drop ;;
esac

for command in git docker python3; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
[[ $(git branch --show-current) == "$branch" ]] || {
    echo "Switch to branch $branch before updating." >&2; exit 1;
}
git diff --quiet && git diff --cached --quiet || {
    echo 'Tracked files have local changes. Resolve them before updating.' >&2; exit 1;
}

docker inspect "$container" > "$inspect_file"
python3 - "$inspect_file" "$project_dir" "$env_file" <<'PY'
import json, os, sys

with open(sys.argv[1], encoding='utf-8') as f:
    obj = json.load(f)[0]
host, mounts = obj['HostConfig'], obj['Mounts']
expected_path = os.path.realpath(os.path.join(sys.argv[2], 'config'))
bindings = host.get('PortBindings') or {}
if (host.get('NetworkMode') != 'bridge'
        or bindings != {'5800/tcp': [{'HostIp': '', 'HostPort': '5800'}]}
        or len(mounts) != 1
        or mounts[0]['Type'] != 'bind'
        or os.path.realpath(mounts[0]['Source']) != expected_path
        or mounts[0]['Destination'] != '/app/config'
        or not mounts[0]['RW']):
    raise SystemExit('Container network, ports or mounts differ from the expected setup; update stopped.')
with open(sys.argv[3], 'w', encoding='utf-8') as f:
    for item in obj['Config']['Env'] or []:
        if '\n' in item or '\r' in item:
            raise SystemExit('An environment variable contains a newline; update stopped.')
        f.write(item + '\n')
PY

if [[ $(git status --porcelain -- .dockerignore) == '?? .dockerignore' ]]; then
    ignore_backup=$(mktemp "${HOME}/twitch-dockerignore-backup.XXXXXX")
    mv -- .dockerignore "$ignore_backup"
    echo "Saved untracked .dockerignore to $ignore_backup"
fi

echo 'Updating source and building image...'
git pull --ff-only origin "$branch"
docker build -t "$build_image" .

pm2_was_running=false
if [[ -n $pm2_name ]] && command -v pm2 >/dev/null; then
    pm2_pid=$(pm2 pid "$pm2_name" 2>/dev/null || true)
    if [[ $pm2_pid =~ ^[1-9][0-9]*$ ]]; then
        pm2 stop "$pm2_name"
        pm2_was_running=true
    fi
fi

old_running=$(docker inspect --format '{{.State.Running}}' "$container")
backup="${container}-backup-$(date +%Y%m%d%H%M%S)-$$"
if [[ $old_running == true ]] && ! docker stop "$container"; then
    if [[ $pm2_was_running == true ]]; then pm2 start "$pm2_name"; fi
    exit 1
fi
if ! docker rename "$container" "$backup"; then
    if [[ $old_running == true ]]; then docker start "$container" >/dev/null; fi
    if [[ $pm2_was_running == true ]]; then pm2 start "$pm2_name"; fi
    exit 1
fi

if ! docker run -d --name "$container" --restart unless-stopped \
    --env-file "$env_file" -p 5800:5800 \
    -v "$project_dir/config:/app/config" "$build_image" || \
    [[ $(docker inspect --format '{{.State.Running}}' "$container") != true ]]; then
    echo 'New container failed to start; restoring the previous setup.' >&2
    docker rm -f "$container" >/dev/null 2>&1 || true
    docker rename "$backup" "$container"
    if [[ $old_running == true ]]; then docker start "$container" >/dev/null; fi
    if [[ $pm2_was_running == true ]]; then pm2 start "$pm2_name"; fi
    exit 1
fi

docker tag "$build_image" "$image"
docker image rm "$build_image" >/dev/null
if [[ $pm2_was_running == true ]]; then pm2 save; fi
echo "Update complete. Previous container: $backup"
docker ps --filter "name=^/${container}$"
docker logs --tail 30 "$container"
