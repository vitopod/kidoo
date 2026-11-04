#!/usr/bin/env python3
"""Create private Kidoo snapshots and restore them to a new staging directory."""
import argparse
import os
from pathlib import Path
import shutil
import sqlite3


FILES = ('server.env', 'jellyfin.json', 'tls/server.crt', 'tls/server.key')


def copy_private(source, destination):
    if source.is_symlink() or not source.is_file():
        raise ValueError('Backup input is not a regular file')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with source.open('rb') as src, os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as dst:
        shutil.copyfileobj(src, dst)


def check_database(path):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity check failed')


def create(source, destination):
    source, destination = Path(source).expanduser().resolve(), Path(destination).expanduser().resolve()
    if source == destination or source in destination.parents:
        raise ValueError('Backup destination must be outside the Kidoo data directory')
    database = source / 'kidoo.db'
    if database.is_symlink() or not database.is_file():
        raise ValueError('Kidoo database is missing or is a symlink')
    destination.mkdir(mode=0o700)
    os.chmod(destination, 0o700)
    target = destination / 'kidoo.db'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    check_database(target)
    for name in FILES:
        path = source / name
        if path.parent != source and path.parent.is_symlink(): raise ValueError('Backup directory is a symlink')
        if path.exists() or path.is_symlink(): copy_private(path, destination / name)
    return destination


def restore(source, destination):
    source, destination = Path(source).expanduser().resolve(), Path(destination).expanduser().resolve()
    if source == destination or source in destination.parents:
        raise ValueError('Restore destination must be outside the backup')
    database = source / 'kidoo.db'
    if database.is_symlink() or not database.is_file(): raise ValueError('Backup database is missing')
    check_database(database)
    destination.mkdir(mode=0o700)
    os.chmod(destination, 0o700)
    copy_private(database, destination / 'kidoo.db')
    for name in FILES:
        path = source / name
        if path.parent != source and path.parent.is_symlink(): raise ValueError('Backup directory is a symlink')
        if path.exists() or path.is_symlink(): copy_private(path, destination / name)
    check_database(destination / 'kidoo.db')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('create', 'restore'):
        command = sub.add_parser(action)
        command.add_argument('--from', dest='source', required=True)
        command.add_argument('--to', dest='destination', required=True)
    args = parser.parse_args()
    try:
        (create if args.action == 'create' else restore)(args.source, args.destination)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f'Backup operation failed: {exc}\n')
    print('Backup created.' if args.action == 'create' else 'Restored to a new staging directory.')


if __name__ == '__main__': main()
