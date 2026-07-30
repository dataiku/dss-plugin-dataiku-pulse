#!/bin/bash

# 1. Define the persistent paths
PERSISTENT_DIR=${PERSISTENT_DIR:-"/home/dataiku/workspace/project-lib-versioned/python/webapps"}
CACHE_DIR="$PERSISTENT_DIR/.npm-persistent"

# Resolve app dir relative to this script so it is relocatable.
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)/frontend"

echo "--- Initializing Persistent NPM Configuration ---"

# 2. Create the cache directory if it doesn't exist
mkdir -p "$CACHE_DIR"

# 3. Set the NPM cache to the persistent volume
# We do not use --global to avoid permission errors on /etc/npmrc
npm config set cache "$CACHE_DIR"

# 4. Verify the configuration
CURRENT_CACHE=$(npm config get cache)
echo "Current NPM Cache Path: $CURRENT_CACHE"

# 5. Navigate to the React project and install/build
if [ -d "$APP_DIR" ]; then
    echo "--- Building React Frontend ---"
    cd "$APP_DIR"
    
    # Optional: Uncomment the next line if you add new packages frequently
    # npm install 
    
    NODE_OPTIONS=--openssl-legacy-provider npm run build
    echo "Build complete!"
else
    echo "Error: Frontend directory not found at $APP_DIR"
fi
