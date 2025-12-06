#!/bin/bash
# Build Tailwind CSS for production
#
# This script uses the standalone Tailwind CLI to compile CSS.
# The CLI binary should be downloaded from https://github.com/tailwindlabs/tailwindcss/releases
#
# Usage:
#   ./scripts/build-css.sh              # Build for development
#   ./scripts/build-css.sh --minify     # Build for production (minified)
#   ./scripts/build-css.sh --watch      # Build and watch for changes

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if tailwindcss binary exists (local or global)
TAILWIND_BIN="./tailwindcss"
if [ ! -f "$TAILWIND_BIN" ]; then
    if command -v tailwindcss &> /dev/null; then
        TAILWIND_BIN="tailwindcss"
    else
        echo -e "${BLUE}Tailwind CSS CLI not found. Installing locally...${NC}"

        # Detect OS and architecture
        OS=$(uname -s | tr '[:upper:]' '[:lower:]')
        ARCH=$(uname -m)

        # Map architecture names
        case $ARCH in
            x86_64)
                ARCH="x64"
                ;;
            aarch64|arm64)
                ARCH="arm64"
                ;;
        esac

        # Determine binary name
        if [[ "$OS" == "darwin" ]]; then
            BINARY="tailwindcss-$OS-$ARCH"
        elif [[ "$OS" == "linux" ]]; then
            BINARY="tailwindcss-$OS-$ARCH"
        else
            echo "Unsupported OS: $OS"
            exit 1
        fi

        # Download latest version to local directory
        echo -e "${BLUE}Downloading Tailwind CSS CLI for $OS-$ARCH...${NC}"
        curl -sLO "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/$BINARY"
        chmod +x "$BINARY"
        mv "$BINARY" "./tailwindcss"

        TAILWIND_BIN="./tailwindcss"
        echo -e "${GREEN}✓ Tailwind CSS CLI installed locally${NC}"
    fi
fi

# Build CSS
INPUT="static/css/tailwind.css"
OUTPUT="static/css/app.css"

echo -e "${BLUE}Building Tailwind CSS...${NC}"
echo -e "  Input: $INPUT"
echo -e "  Output: $OUTPUT"
echo

if [[ "$1" == "--watch" ]]; then
    echo -e "${BLUE}Watching for changes (Ctrl+C to stop)...${NC}"
    "$TAILWIND_BIN" -i "$INPUT" -o "$OUTPUT" --watch
elif [[ "$1" == "--minify" ]]; then
    echo -e "${BLUE}Building minified CSS for production...${NC}"
    "$TAILWIND_BIN" -i "$INPUT" -o "$OUTPUT" --minify
    echo -e "${GREEN}✓ Production CSS built successfully${NC}"
else
    "$TAILWIND_BIN" -i "$INPUT" -o "$OUTPUT"
    echo -e "${GREEN}✓ Development CSS built successfully${NC}"
fi
