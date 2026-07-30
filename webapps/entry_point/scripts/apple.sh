#!/bin/bash

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$APP_DIR/frontend"

echo "--- 🚀 Optimized Frontend Build ---"

cd "$FRONTEND_DIR" || { echo "Error: Path not found"; exit 1; }

# Since react-scripts is now in the Docker image, 
# we only run npm install if you added new custom packages
if [ ! -d "node_modules" ]; then
    echo "📦 Installing project-specific packages..."
    npm install
fi

echo "🏗️ Building production assets..."
NODE_OPTIONS=--openssl-legacy-provider npm run build

echo "✅ Done! Just Sync and Restart Entrypoints."
