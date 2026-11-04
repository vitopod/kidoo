#!/usr/bin/env python3
"""Set up a new, protected Kidoo installation interactively on this computer."""
import argparse
import getpass
import os
from pathlib import Path
import shlex
import sqlite3
import ssl
import subprocess
import sys

import jellyfin
from kidoo_server import init_db
from security import SecurityError, SecurityStore


def ask_path(prompt, must_be_directory=False):
    while True:
        value=input(prompt).strip()
        if not value:
            print('Please enter a path.')
            continue
        path=Path(value).expanduser().resolve()
        if path.is_dir() if must_be_directory else path.is_file(): return path
        print('That path is not an existing '+('folder.' if must_be_directory else 'file.'))


def ask_pin():
    while True:
        first=getpass.getpass('Choose a parent PIN (6–12 digits): ')
        second=getpass.getpass('Repeat the PIN: ')
        if first!=second: print('The PINs did not match. Try again.')
        elif not (first.isascii() and first.isdigit() and 6<=len(first)<=12): print('Use 6–12 digits.')
        else: return first


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',default=str(Path.home()/'.kidoo'),help='New Kidoo data directory')
    parser.add_argument('--port',type=int,default=8765,help='Kidoo HTTPS port (default: 8765)')
    args=parser.parse_args()
    if not 1<=args.port<=65535: parser.error('Port must be between 1 and 65535')
    data=Path(args.data_dir).expanduser().resolve()
    if (data/'kidoo.db').exists():
        parser.error('This data directory already has a Kidoo database. Use the existing setup instructions or choose a new directory.')
    print('Kidoo setup: create a parent, protect your server and connect your TV.')
    print('You need an existing videos folder and an HTTPS certificate trusted by your TV.')
    media=ask_path('Videos folder: ',True)
    while True:
        cert=ask_path('HTTPS certificate chain (PEM): ')
        key=ask_path('HTTPS private key (PEM): ')
        try:
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert),str(key))
            break
        except (ssl.SSLError,OSError) as error: print('Certificate or key could not be loaded:',error)
    print('The certificate hostname must be trusted and reachable by the TV and browser; renew it before expiry.')
    while True:
        name=input('Parent administrator name: ').strip()
        if name and len(name)<=40: break
        print('Enter a name of 1–40 characters.')
    pin=ask_pin()
    data.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(data/'kidoo.db',os.O_CREAT | os.O_EXCL | os.O_WRONLY,0o600)
    os.close(fd)
    init_db(data/'kidoo.db')
    security=SecurityStore(data/'kidoo.db')
    security.create_administrator(name)
    security.set_pin(pin)
    del pin
    print('Parent administrator and PIN saved in the new Kidoo data directory.')
    if input('Connect an existing Jellyfin account now? [y/N] ').strip().lower() in ('y','yes'):
        server=input('Jellyfin server URL: ').strip()
        username=input('Jellyfin username: ').strip()
        password=getpass.getpass('Jellyfin password: ')
        try:
            mapping=jellyfin.authenticate(server,username,password)
            jellyfin.save_config(data,'family',mapping)
            print('Jellyfin connected to the Family profile.')
        except (jellyfin.JellyfinError,ValueError,OSError) as error:
            print('Jellyfin was not connected:',error)
            print('You can connect it later using the setup guide.')
        finally: del password
    command=[sys.executable,str(Path(__file__).resolve().parent/'kidoo_server.py'),
             '--media-dir',str(media),'--data-dir',str(data),'--host','0.0.0.0','--port',str(args.port),
             '--protected-mode','--tls-cert',str(cert),'--tls-key',str(key)]
    launcher=data/'start-kidoo.sh'
    try:
        launcher_fd=os.open(launcher,os.O_CREAT | os.O_EXCL | os.O_WRONLY,0o700)
        os.fchmod(launcher_fd,0o700)
        with os.fdopen(launcher_fd,'w') as script:
            script.write('#!/bin/sh\nexec '+shlex.join(command)+'\n')
        print('\nTo start Kidoo again, run: '+shlex.quote(str(launcher)))
    except FileExistsError:
        print('\nAn existing start script was not changed. Start Kidoo with:')
        print(shlex.join(command))
    print(f'\nOpen https://YOUR_CERTIFICATE_HOSTNAME:{args.port}/dashboard and sign in with the parent name and PIN.')
    print('Choose the profile and approve videos before your child starts watching.')
    print('The server must be running when the TV redeems its pairing code.')
    if input('Create a one-time TV pairing code now? [y/N] ').strip().lower() in ('y','yes'):
        while True:
            label=input('TV name [Family TV]: ').strip() or 'Family TV'
            try:
                code=security.create_pairing('family',label)
                break
            except SecurityError as error:
                print(error)
        print('Pairing code: '+code)
        print('Enter it in the Kidoo TV app within five minutes. Never share it publicly.')
    print('You can also create another code from Devices and child access in the parent dashboard.')
    print('Keep the computer running while watching; configure certificate renewal and private backups.')
    if input('Start Kidoo now? [y/N] ').strip().lower() in ('y','yes'):
        return subprocess.call(command)
    return 0


if __name__=='__main__':
    try: sys.exit(main())
    except (KeyboardInterrupt,EOFError): print('\nSetup stopped.');sys.exit(1)
    except (SecurityError,OSError) as error: print('Setup could not finish:',error,file=sys.stderr);sys.exit(1)
