#!/usr/bin/env python3
"""Create a uniquely named Kidoo backup without deleting existing snapshots."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import sys
from backup import create


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--backup-dir', required=True)
    args = parser.parse_args()
    root = Path(args.backup_dir).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if shutil.disk_usage(root).free < 1024**3:
            raise OSError('Less than 1 GiB free; backup was not started')
        name = datetime.now(timezone.utc).strftime('kidoo-%Y%m%dT%H%M%S.%fZ')
        create(args.data_dir, root / name)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f'Backup failed: {error}', file=sys.stderr)
        return 1
    print('Daily backup created.')
    return 0


if __name__ == '__main__': raise SystemExit(main())
