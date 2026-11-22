# Running the fork on a server

This fork follows `fireph/TwitchDropsMiner` (branch `webui`) and runs the WebUI
under systemd on an Ubuntu server, one instance per Twitch account.

```
fireph/webui ──(Sync Upstream workflow: merge + tests)──> auuruum/webui ──(update timer on the server)──> instances
```

## 1. Upstream sync (GitHub Actions)

`.github/workflows/sync-upstream.yml` runs every 6 hours and on demand
(Actions → Sync Upstream → Run workflow). It:

1. fetches `fireph/webui` and merges it into `webui` with a merge commit;
2. validates the language files and runs `pytest tests/`;
3. pushes `webui` only if the merge was clean and every test passed.

On a conflict or a failing test nothing is pushed. The run fails (GitHub emails
you about failed scheduled runs), its summary holds the conflict list or the
test output, and, if issues are enabled on the fork, an issue
"Upstream sync blocked" is opened or commented on. Fix it locally:

```sh
git fetch upstream
git switch webui
git merge upstream/webui          # resolve conflicts
uv run --group nicegui --group test pytest tests/
git push origin webui
```

Pushes from the default token cannot change files in `.github/workflows/`. If
upstream edits a workflow, the push step fails; add a fine-grained token with
**Contents** and **Workflows** read/write on this repository as the
`SYNC_TOKEN` secret to allow it.

`upstream-merge.yml` is upstream's own workflow (it merges DevilXD/master into
fireph's fork). It fails here for lack of `PAT_TOKEN`; disable it in the fork
(Actions → Merge Upstream → ⋯ → Disable workflow) rather than editing it, so the
file never conflicts with upstream.

## 2. Deploy (`update-server.sh`)

Each checkout on the server is one instance. Run `./update-server.sh` in it
once; that:

- creates `env/` (virtualenv) and installs `requirements-nicegui.txt` when the
  requirements change;
- installs `twitch-drops-<dir>.service` (CPU and memory limited) and restarts it;
- installs `twitch-drops-<dir>-update.timer`, which runs
  `update-server.sh --auto` every 30 minutes.

The server only ever pulls `origin/webui`, and the sync workflow only pushes
tested merges there, so an untested upstream change never reaches it.

After each restart the script waits for `GET /health` to return 200 (default
120 s, `HEALTH_TIMEOUT=`). If it does not, the script resets the checkout to
the commit that ran before, restarts that, and records the bad commit in
`.git/tdm-bad-commit`. The timer skips that commit; a manual
`./update-server.sh` retries it. Being logged out of Twitch does not count as
unhealthy.

The script is safe to rerun: unit files are rewritten only when they change, a
lock stops the timer and a manual run from overlapping, and `--auto` does
nothing when `origin/webui` has not moved.

Useful commands (replace `<dir>` with the checkout's directory name):

```sh
journalctl -u twitch-drops-<dir> -f              # miner logs
journalctl -u twitch-drops-<dir>-update -n 50    # update attempts
systemctl list-timers 'twitch-drops-*'
AUTO_UPDATE=0 ./update-server.sh                 # turn automatic updates off
```

Automatic updates need passwordless `sudo` for the user (the default for
`ubuntu` on Ubuntu cloud images).

## 3. Several Twitch accounts

The miner keeps everything next to `main_webui.py` (see `constants.py`):

| Path | Contents |
|---|---|
| `config/cookies.jar` | Twitch login session |
| `config/settings.json` | priority/exclude lists, language, other settings |
| `config/server.env` | environment for the systemd service (port, auth, browser) |
| `config/webui_auth.db` | WebUI users when `WEBUI_AUTH=1` |
| `cache/` | image cache |
| `lock.file` | stops a second miner in the same directory |

These paths are derived from the script's location and cannot be pointed
elsewhere without changing core files, so each account gets its own checkout.
Each checkout has its own login, cookies, settings, service, timer and port.

Add an account (here `alt1` on port 5801):

```sh
git clone -b webui https://github.com/auuruum/TwitchDropsMiner.git ~/TwitchDropsMiner-alt1
mkdir -p ~/TwitchDropsMiner-alt1/config
# Start from the main instance's settings, then give it its own port.
grep -v '^WEBUI_PORT=' ~/TwitchDropsMiner/config/server.env > ~/TwitchDropsMiner-alt1/config/server.env
echo 'WEBUI_PORT=5801' >> ~/TwitchDropsMiner-alt1/config/server.env
~/TwitchDropsMiner-alt1/update-server.sh
```

Then open `http://<server>:5801` and log in with the second Twitch account.
The script refuses to start an instance whose port another instance already
uses. If a reverse proxy fronts the WebUI, add a route for the new port.

Remove an account:

```sh
sudo systemctl disable --now twitch-drops-TwitchDropsMiner-alt1 twitch-drops-TwitchDropsMiner-alt1-update.timer
sudo rm -f /etc/systemd/system/twitch-drops-TwitchDropsMiner-alt1.service \
  /etc/systemd/system/twitch-drops-TwitchDropsMiner-alt1-update.{service,timer}
sudo systemctl daemon-reload
rm -rf ~/TwitchDropsMiner-alt1
```

Each instance uses its own virtualenv and Python process; the service caps each
one at `CPU_QUOTA` (50%) and `MEMORY_MAX` (1G) by default.

Do not watch a stream in a browser on an account the miner is using; Twitch
then reports wrong progress (see README).
