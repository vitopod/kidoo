#!/bin/sh
# Start the guided setup without installing system packages or changing an existing server.
set -eu
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Kidoo needs Python 3.9 or newer. Install Python 3 and run this command again.' >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    printf '%s\n' 'Kidoo needs Python 3.9 or newer.' >&2
    exit 1
fi
script_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
exec python3 "$script_dir/server/setup_wizard.py" "$@"
