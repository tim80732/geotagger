#!/bin/bash
# Update Geotagger on Unraid from GitHub: pull, rebuild the image, recreate the container.
#   bash /mnt/user/appdata/geotagger/src/unraid/deploy.sh
set -euo pipefail

SRC="${SRC:-/mnt/user/appdata/geotagger/src}"
NAME="${CONTAINER:-Geotagger}"
REBUILD=/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/rebuild_container

cd "$SRC"

# Don't restart in the middle of a dry run or a write
if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  busy=$(docker exec "$NAME" python -c "import sqlite3; c = sqlite3.connect('/data/geotagger.db'); print(c.execute(\"SELECT COUNT(*) FROM jobs WHERE status IN ('scanning', 'writing', 'queued')\").fetchone()[0])" 2>/dev/null || echo 0)
  if [ "$busy" != 0 ]; then
    echo "A job is running in $NAME. Pause it or let it finish, then run this again." >&2
    exit 1
  fi
fi

git pull --ff-only
echo "Now at $(git log -1 --format='%h %s')"
docker build -t geotagger:latest .
"$REBUILD" "$NAME"
docker image prune -f >/dev/null
echo "Done."
