#!/usr/bin/env python3
"""Try Kidoo locally or set up a new protected home server."""
import argparse
import getpass
import os
from pathlib import Path
import shlex
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import warnings

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


def ask_mode():
    while True:
        choice=input('Choose [1] local preview or [2] protected home setup (default 2): ').strip()
        if choice in ('', '2'): return 'protected'
        if choice == '1': return 'preview'
        print('Enter 1 or 2.')


def preview(media, port):
    print('\nLocal preview: only this computer can connect. It cannot pair a TV or protect child access.')
    print('Preview profiles and viewing history are temporary and will be removed when you stop it.')
    print(f'Open http://127.0.0.1:{port}/ in your browser. Press Ctrl+C to stop the preview.')
    with tempfile.TemporaryDirectory(prefix='kidoo-preview-') as data:
        command=[sys.executable,str(Path(__file__).resolve().parent/'kidoo_server.py'),
                 '--media-dir',str(media),'--data-dir',data,'--host','127.0.0.1','--port',str(port)]
        environment=os.environ.copy()
        environment.pop('KIDOO_PASSWORD',None)
        try:
            return subprocess.call(command,env=environment)
        except KeyboardInterrupt:
            print('\nPreview stopped.')
            return 0


def certificate_info(cert):
    """Best-effort inspection; trust must still be established on each device."""
    try:
        return ssl._ssl._test_decode_cert(str(cert))
    except (AttributeError, OSError, ssl.SSLError, ValueError):
        return None


def certificate_problem(info, now=None):
    if not info: return None
    now=time.time() if now is None else now
    try:
        if info.get('notBefore') and ssl.cert_time_to_seconds(info['notBefore']) > now:
            return 'This certificate is not valid yet.'
        if info.get('notAfter') and ssl.cert_time_to_seconds(info['notAfter']) <= now:
            return 'This certificate has expired.'
    except (ValueError, OverflowError):
        return 'The certificate validity dates could not be checked.'
    return None


def port_available(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host,port))
            listener.listen(1)
        return True
    except OSError:
        return False


def protected_setup_supported():
    return os.name != 'nt'


def ask_hostname(cert):
    info=certificate_info(cert)
    names=[value for kind,value in (info or {}).get('subjectAltName',()) if kind in ('DNS','IP Address') and not value.startswith('*.')]
    if info and not info.get('subjectAltName'):
        names += [value for group in info.get('subject',()) for key,value in group if key=='commonName' and not value.startswith('*.')]
    default=names[0] if len(names)==1 else ''
    if names: print('Certificate names: '+', '.join(names))
    while True:
        host=input('Certificate hostname used by your TV'+(f' [{default}]' if default else '')+': ').strip() or default
        if not host or any(c in host for c in '/:@?#[] ') or len(host)>253:
            print('Enter the certificate hostname or IPv4 address, without https:// or a port.')
            continue
        if info:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', DeprecationWarning)
                    ssl.match_hostname(info,host)
            except (ssl.CertificateError, ValueError):
                print('That hostname does not match this certificate. Try a name covered by it.')
                continue
        else:
            print('Could not inspect certificate names. Check the hostname matches the certificate.')
        return host


def main():
    if sys.version_info < (3,9):
        print('Kidoo needs Python 3.9 or newer.',file=sys.stderr)
        return 1
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',default=str(Path.home()/'.kidoo'),help='New Kidoo data directory')
    parser.add_argument('--port',type=int,default=8765,help='Kidoo HTTPS port (default: 8765)')
    args=parser.parse_args()
    if not 1<=args.port<=65535: parser.error('Port must be between 1 and 65535')
    data=Path(args.data_dir).expanduser().resolve()
    print('Welcome to Kidoo. Set up a protected family server or try a temporary local preview.')
    mode=ask_mode()
    if mode=='protected' and not protected_setup_supported():
        parser.error('Protected setup is not yet supported on Windows. Choose local preview, or run the protected server on macOS or Linux.')
    if mode=='protected' and (data/'kidoo.db').exists():
        parser.error('This data directory already has a Kidoo database. Use the existing setup instructions or choose a new directory.')
    if not port_available('127.0.0.1',args.port) or (mode=='protected' and not port_available('0.0.0.0',args.port)):
        parser.error(f'Port {args.port} is unavailable. Stop the other server or run setup again with --port 8766 (or another free port).')
    media=ask_path('Videos folder: ',True)
    if mode=='preview': return preview(media,args.port)
    print('\nProtected TV setup requires an HTTPS certificate and private key already trusted by the TV and browser.')
    while True:
        cert=ask_path('HTTPS certificate chain (PEM): ')
        key=ask_path('HTTPS private key (PEM): ')
        try:
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert),str(key))
            problem=certificate_problem(certificate_info(cert))
            if problem:
                print(problem+' Choose a currently valid certificate.')
                continue
            break
        except (ssl.SSLError,OSError) as error: print('Certificate or key could not be loaded:',error)
    hostname=ask_hostname(cert)
    print('The certificate must be trusted and reachable by the TV and browser; renew it before expiry.')
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
    print(f'\nWhen Kidoo is running, open https://{hostname}:{args.port}/dashboard and sign in with the parent name and PIN.')
    print('Choose the profile and approve videos before your child starts watching.')
    print('The server must be running when the TV redeems its pairing code. Install the Kidoo TV app separately.')
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
    print('For recovery, backups, certificate guidance and TV setup, see docs/SETUP.md.')
    if input('Start Kidoo now? [y/N] ').strip().lower() in ('y','yes'):
        return subprocess.call(command)
    return 0


if __name__=='__main__':
    try: sys.exit(main())
    except (KeyboardInterrupt,EOFError): print('\nSetup stopped.');sys.exit(1)
    except (SecurityError,OSError) as error: print('Setup could not finish:',error,file=sys.stderr);sys.exit(1)
