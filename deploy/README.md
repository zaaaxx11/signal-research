# Host deployment examples

Docker (`Dockerfile` + `docker-compose.yml` at the repo root) is the reference
way to run Ztockz anywhere. These systemd units are the alternative when a
container runtime is not available.

They assume:

- code at `/opt/ztockz` with its virtualenv at `/opt/ztockz/.venv`;
- environment at `/etc/ztockz/engine.env` (`MEE_DATA_DIR`, optional overrides);
- state at `/var/lib/ztockz` (`cache/`, `backups/`, `engine-snapshot.json`);
- a `ztockz` system user that owns the state directory.

Install:

```bash
sudo install -d -o ztockz -g ztockz /var/lib/ztockz/{cache,backups}
sudo install -m 0644 deploy/ztockz-retention.service deploy/ztockz-retention.timer \
  deploy/ztockz-backup.service deploy/ztockz-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ztockz-retention.timer ztockz-backup.timer
```

Every command also runs by hand; no flag is required when the environment is
set, because `app/engine/paths.py` resolves `MEE_ENGINE_CACHE_DIR`,
`MEE_ENGINE_SNAPSHOT` and `MEE_BACKUP_DIR` with `./data` as the last fallback:

```bash
/opt/ztockz/.venv/bin/python -m app.engine.ops cache
/opt/ztockz/.venv/bin/python -m app.engine.ops retention --max-age-days 30 --max-bytes 2000000000
/opt/ztockz/.venv/bin/python -m app.engine.ops backup --keep 7
/opt/ztockz/.venv/bin/python -m app.engine.ops smoke --base-url http://127.0.0.1:8099
```
