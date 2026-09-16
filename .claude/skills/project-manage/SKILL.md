---
name: project-manage
description: "Cancel, rebuild, and/or restart a podcracy project. Trigger when the user asks to cancel a project, restart a project, rebuild the worker, or any combination (e.g. 'cancel and restart', 'rebuild and rerun', 'stop the current job'). Also use when the user wants to re-queue a cancelled or interrupted project."
---

# Project Management

Handles the full cancel → rebuild → restart lifecycle for podcracy projects.

## Key facts

- API base: `http://localhost:8080`
- Projects volume (host): `data/projects/<project-id>/`
- Worker container: `podcracy-app-worker-1`
- Docker Compose file: `docker-compose.yml` in the repo root

## Step 1 — Identify the target project

If the user named a project ID (e.g. `project-20260916-211904-fbf66f58`), use it.

Otherwise find the most recent one:

```bash
ls data/projects/ | sort | tail -1
```

Check its current state:

```bash
curl -s http://localhost:8080/api/projects/<project-id> \
  | python3 -c "import sys,json; d=json.load(sys.stdin); s=d['status']; print('state:', s['state'], '| stage:', s.get('stage'))"
```

## Step 2 — Cancel (if running or cancelling)

Only cancel if the state is `running`, `queued`, or `cancelling`.

```bash
curl -s -X POST http://localhost:8080/api/projects/<project-id>/cancel \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['status']['state'])"
```

Then wait for `cancelled`:

```bash
for i in $(seq 1 24); do
  state=$(curl -s http://localhost:8080/api/projects/<project-id> \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['status']['state'])" 2>/dev/null)
  echo "$(date +%T) state=$state"
  [ "$state" = "cancelled" ] && break
  sleep 5
done
```

## Step 3 — Rebuild the worker (if code changed)

Skip this step if the user only wants to re-queue without a code change.

```bash
docker compose build worker
```

Then restart the container so the new image is active:

```bash
docker compose up -d worker
```

## Step 4 — Re-queue the project

There is no API endpoint for re-queueing a cancelled project. Write status.json directly — this is safe because the projects directory is a shared volume and the API does the same thing internally for new projects.

```bash
# Clear any stale cancel sentinel
rm -f data/projects/<project-id>/cancel.request

# Set state to queued
python3 -c "
import json, datetime
status = {
    'project_id': '<project-id>',
    'state': 'queued',
    'stage': 'queued',
    'progress': 0,
    'message': 'Waiting for worker',
    'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat()
}
with open('data/projects/<project-id>/status.json', 'w') as f:
    json.dump(status, f, indent=2)
print('queued')
"
```

## Step 5 — Confirm pickup

Wait a few seconds then verify the worker picked it up:

```bash
sleep 5
curl -s http://localhost:8080/api/projects/<project-id> \
  | python3 -c "import sys,json; d=json.load(sys.stdin); s=d['status']; print('state:', s['state'], '| stage:', s.get('stage'), '| message:', s.get('message'))"
```

Expected: `state: running | stage: transcribe | message: Running transcribe`

Also confirm the model in the new orchestrator log:

```bash
grep -i "model\|provider" data/projects/<project-id>/input/.log/e*_log_pd-00-orchestrator.py_*.log \
  | tail -5
```

## Step 6 — Report

Tell the user:
- Which steps were performed (cancel / rebuild / restart)
- The confirmed running state and model (e.g. `local-whisper / large`)
- Any warnings (e.g. model was already correct, rebuild was skipped)
