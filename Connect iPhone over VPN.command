#!/bin/sh
# macOS Finder launcher. The sleep assertion lasts only for this session.
set -eu
cd "$(dirname "$0")"
for py in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        exec /usr/bin/caffeinate -i "$py" phone_vpn.py
    fi
done
echo "ต้องใช้ Python 3.11 ขึ้นไป"
exit 1
