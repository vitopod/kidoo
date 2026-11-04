#!/usr/bin/env python3
"""Convert an earlier Kidoo database, with an offline backup and rollback path.

The default operation creates a separate copy. The explicit offline upgrade
requires the operator to stop all server processes and provide a new backup
path. Online history is retained as inaccessible archival data.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat

import your_tv


def convert_channels(db):
    """Convert saved channels and retire incompatible queue entries atomically.

    The caller owns the transaction. Other tables, including historical online
    activity and progress, are deliberately retained in the copied database.
    """
    rows = db.execute('SELECT profile_id,id,definition FROM your_tv_definitions').fetchall()
    converted = []
    for profile, channel, value in rows:
        definition = your_tv.community_definition(json.loads(value))
        if definition['id'] != channel:
            raise ValueError('Saved channel ID does not match its definition')
        converted.append((json.dumps(definition, ensure_ascii=False), profile, channel))
    db.executemany('UPDATE your_tv_definitions SET definition=? WHERE profile_id=? AND id=?', converted)
    # Only the destination is changed. The original database retains every row.
    db.execute("DELETE FROM your_tv_queue WHERE item_key LIKE 'online:%' OR youtube_id<>''")
    return len(converted)


def create_copy(source, destination):
    source, destination = Path(source).expanduser(), Path(destination).expanduser()
    if source.is_symlink() or not source.is_file():
        raise ValueError('Source must be an existing regular file, not a symlink')
    if destination.is_symlink() or destination.exists():
        raise ValueError('Destination already exists; choose a new path')
    source = source.resolve(strict=True)
    destination = destination.parent.resolve(strict=True) / destination.name
    if source == destination:
        raise ValueError('Source and destination must differ')
    # O_EXCL prevents a race from overwriting an existing file.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30) as src:
            with sqlite3.connect(destination, timeout=30) as dst:
                src.backup(dst)
                dst.execute('BEGIN IMMEDIATE')
                try:
                    count = convert_channels(dst)
                    dst.commit()
                except Exception:
                    dst.rollback()
                    raise
    except Exception as error:
        # Leave the destination in place so no existing or copied data is
        # destroyed automatically. The operator can inspect it or use Trash.
        raise RuntimeError('Copy or conversion failed; the destination was retained for inspection') from error
    return count


REQUIRED_TABLES = {
    'profiles', 'progress', 'approvals', 'local_preferences', 'viewing_limits',
    'activity_sessions', 'activity_minutes', 'content_reviews', 'security_devices',
    'security_administrators', 'security_pin', 'security_pairing', 'security_parent_sessions',
    'security_profile_approvals', 'security_profile_blocks', 'security_approval_migrations',
    'your_tv_definitions', 'your_tv_queue', 'your_tv_initialized', 'your_tv_sessions',
    'your_tv_offers', 'your_tv_latest', 'your_tv_preserved', 'your_tv_activity_queue',
    'jellyfin_approvals', 'jellyfin_favourites', 'jellyfin_sessions', 'schema_flags',
}
VERSION = 'community_migration_v1'


def _inspect(db):
    if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
        raise ValueError('Database integrity check failed')
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing=REQUIRED_TABLES-tables
    if missing:
        raise ValueError('Unsupported database schema; missing tables: '+', '.join(sorted(missing)))


def _unchanged(path, before):
    after=path.stat()
    return (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)==before


def upgrade_stopped(database, backup, *, server_stopped=False):
    """Upgrade one stopped installation's database; preserve an unmodified backup.

    A sidecar check and file identity checks catch common mistakes, but do not
    prove the service is stopped. The operator must stop every writer first.
    Configuration and media are never opened or modified by this operation.
    """
    if server_stopped is not True:
        raise ValueError('Stop all Kidoo processes and confirm the server is stopped')
    database=Path(database).expanduser()
    backup=Path(backup).expanduser()
    if database.is_symlink() or not database.is_file():
        raise ValueError('Database must be an existing regular file, not a symlink')
    if backup.exists() or backup.is_symlink(): raise ValueError('Backup path already exists')
    database=database.resolve(strict=True)
    backup=backup.parent.resolve(strict=True)/backup.name
    if database == backup: raise ValueError('Backup and database must differ')
    def check_sidecars():
        if any(Path(str(database)+suffix).exists() for suffix in ('-wal','-shm','-journal')):
            raise ValueError('SQLite sidecar exists; stop all writers and checkpoint before upgrading')
    check_sidecars()
    st=database.stat();before=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as src:
        _inspect(src)
    # An exclusive backup path ensures existing backups are never overwritten.
    fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    replaced=False
    try:
        with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(backup) as dst:
            src.backup(dst)
            _inspect(dst)
        with open(backup,'rb') as f: os.fsync(f.fileno())
        stage=database.with_name(database.name+'.community-staged')
        count=create_copy(backup,stage)
        with sqlite3.connect(stage) as converted:
            _inspect(converted)
            converted.execute('INSERT OR IGNORE INTO schema_flags(name) VALUES (?)',(VERSION,))
            converted.commit()
            _inspect(converted)
        os.chmod(stage,stat.S_IMODE(st.st_mode))
        with open(stage,'rb') as f: os.fsync(f.fileno())
        check_sidecars()
        if not _unchanged(database,before): raise ValueError('Source changed during migration; replacement refused')
        os.replace(stage,database)
        replaced=True
        dirfd=os.open(database.parent,os.O_RDONLY)
        try: os.fsync(dirfd)
        finally: os.close(dirfd)
        return count
    except Exception as error:
        state='Database was replaced but final durability verification failed' if replaced else 'Database was not replaced'
        raise RuntimeError(state+'; retain the backup and any staged file for recovery') from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Existing database to copy, never modified')
    parser.add_argument('destination', type=Path, help='New database path; must not exist')
    parser.add_argument('--upgrade-stopped', action='store_true', help='Replace source atomically; destination is a new backup file')
    parser.add_argument('--server-stopped', action='store_true', help='Confirm every Kidoo process and database writer is stopped')
    args = parser.parse_args()
    try:
        count = upgrade_stopped(args.source,args.destination,server_stopped=args.server_stopped) if args.upgrade_stopped else create_copy(args.source, args.destination)
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'{error}\n')
    print(f'Copied database and converted {count} channel definitions.')
    print('Keep the original backup private. Configuration and media were not changed.')


if __name__ == '__main__':
    main()
