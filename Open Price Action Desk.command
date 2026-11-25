#!/bin/sh
# Double-click in Finder on macOS. Keep the Terminal open while using the desk.
set -eu
cd "$(dirname "$0")"
if curl -sS --max-time 2 http://127.0.0.1:8765/api/session 2>/dev/null | grep -q '"authenticated"'; then
    open http://127.0.0.1:8765
    exit 0
fi
for python_bin in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if "$python_bin" -c 'import sys; sys.exit(sys.version_info < (3,11))' 2>/dev/null; then
        "$python_bin" app.py --open
        exit $?
    fi
done
echo "Install Python 3.11 or newer, then open this launcher again."
exit 1
