#!/usr/bin/env python3
"""Manage parent credentials locally as the Kidoo service user."""
import argparse
import getpass
from pathlib import Path
import sys
from security import SecurityStore, SecurityError


def main():
    parser=argparse.ArgumentParser(description='Manage Kidoo administrator setup locally')
    parser.add_argument('--data-dir',default=str(Path.home()/'.kidoo'))
    sub=parser.add_subparsers(dest='action',required=True)
    create=sub.add_parser('create-administrator');create.add_argument('--name',required=True)
    sub.add_parser('set-pin')
    args=parser.parse_args();db=Path(args.data_dir).expanduser()/'kidoo.db'
    if not db.is_file(): parser.error('Kidoo database does not exist; start Kidoo first')
    try:
        store=SecurityStore(db)
        if args.action=='create-administrator':
            store.create_administrator(args.name)
            print('Administrator created without a password. Set a parent PIN before protected mode.')
        else:
            first=getpass.getpass('New parent PIN (6–12 digits): ')
            second=getpass.getpass('Repeat parent PIN: ')
            if first!=second: raise SecurityError('PINs did not match')
            store.set_pin(first)
            print('Parent PIN updated. Restart Kidoo to end existing browser sessions.')
    except SecurityError as error:
        print(str(error),file=sys.stderr);return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
