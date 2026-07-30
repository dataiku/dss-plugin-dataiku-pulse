#!/bin/bash

# 1. Define Paths
# Resolve app paths relative to this script so it is relocatable.
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$APP_DIR/frontend"
REACT_BIN="$FRONTEND_DIR/node_modules/.bin/react-scripts"

# Keep npm cache on a persistent volume when available.
PERSISTENT_CACHE=${PERSISTENT_CACHE:-"/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse/.local/npm-cache"}

echo "--- Fixing NPM Permissions ---"
mkdir -p "$PERSISTENT_CACHE"
# Tell npm to stop looking at the root-owned /home/dataiku/.npm folder
npm config set cache "$PERSISTENT_CACHE" --global false

echo "--- Starting Frontend Build Process ---"

cd "$FRONTEND_DIR" || { echo "Error: Frontend directory not found"; exit 1; }

# 2. Check if react-scripts exists
if [ -f "$REACT_BIN" ]; then
    echo "✅ react-scripts found. Skipping npm install..."
else
    echo "⚠️ react-scripts missing. Installing dependencies..."
    # Now it will use the persistent cache we just configured
    npm install
fi

# 3. Run the Build
echo "--- Compiling React Assets ---"
NODE_OPTIONS=--openssl-legacy-provider npm run build

cd "$APP_DIR" || exit 0
echo "✅ Done! Remember to Sync and Restart."
