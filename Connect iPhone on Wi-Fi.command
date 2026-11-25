#!/bin/sh
# Open in Finder on the Mac. Keep Terminal open while using the Wi-Fi listener.
set -eu
cd "$(dirname "$0")"
for python_bin in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if "$python_bin" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        exec "$python_bin" phone_wifi.py
    fi
done
echo "Install Python 3.11 or newer, then open this launcher again."
exit 1
