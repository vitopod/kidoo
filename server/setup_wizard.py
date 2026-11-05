#!/usr/bin/env python3
"""Try Kidoo locally or set up a new protected home server."""
import argparse
import getpass
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import warnings

import jellyfin
import autostart
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


def issue_tailscale_certificate(hostname, data, *, run=subprocess.run):
    """Issue new files without replacing any existing certificate or key."""
    if not isinstance(hostname,str) or not re.fullmatch(r'[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.ts\.net',hostname):
        raise ValueError('Enter the full Tailscale HTTPS hostname ending in .ts.net')
    data=Path(data)
    data.mkdir(parents=True,exist_ok=True,mode=0o700)
    tls=data/'tls'
    if tls.is_symlink(): raise ValueError('The certificate directory cannot be a symlink')
    tls.mkdir(exist_ok=True,mode=0o700)
    if tls.stat().st_mode & 0o077: raise ValueError('The certificate directory must be private to this user')
    cert,key=tls/'server.crt',tls/'server.key'
    if cert.exists() or cert.is_symlink() or key.exists() or key.is_symlink():
        raise FileExistsError('Certificate files already exist; choose the existing-certificate option')
    with tempfile.TemporaryDirectory(prefix='.cert-',dir=tls) as temp:
        pending_cert,pending_key=Path(temp)/'server.crt',Path(temp)/'server.key'
        run(['tailscale','cert','--cert-file',str(pending_cert),'--key-file',str(pending_key),hostname],check=True)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(pending_cert),str(pending_key))
        info=certificate_info(pending_cert)
        if certificate_problem(info): raise ValueError(certificate_problem(info))
        if info:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',DeprecationWarning)
                ssl.match_hostname(info,hostname)
        for source,target in ((pending_key,key),(pending_cert,cert)):
            fd=os.open(target,os.O_CREAT | os.O_EXCL | os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as output, source.open('rb') as input_file:
                shutil.copyfileobj(input_file,output)
    return cert,key


def ask_certificates(data):
    while True:
        choice=input('HTTPS certificate: [1] use existing files or [2] issue with Tailscale (default 1): ').strip()
        if choice in ('','1'):
            return ask_path('HTTPS certificate chain (PEM): '),ask_path('HTTPS private key (PEM): ')
        if choice!='2':
            print('Enter 1 or 2.')
            continue
        print('Tailscale must already be installed, signed in and allowed to issue HTTPS certificates.')
        print('Issuing a certificate publishes the device hostname and tailnet domain in public certificate transparency logs.')
        print('Your TV needs network access to this server and a trusted certificate; the TV does not have to run Tailscale.')
        if input('Proceed with Tailscale certificate issuance? [y/N] ').strip().lower() not in ('y','yes'):
            continue
        hostname=input('Full Tailscale HTTPS hostname (ending in .ts.net): ').strip()
        try:
            return issue_tailscale_certificate(hostname,data)
        except (OSError,ValueError,ssl.SSLError,subprocess.CalledProcessError) as error:
            print('Could not issue a certificate:',error)
            print('No existing certificate was replaced. Check Tailscale permissions or use existing certificate files.')


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
    print('Protected setup: choose your videos and HTTPS certificate, create a parent PIN and child profile, then review content and pair a device.')
    print('You can connect Jellyfin now or later from the protected parent dashboard. Your existing Kidoo database will not be replaced.')
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
        cert,key=ask_certificates(data)
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
    while True:
        child_name=input('First child profile name [Family]: ').strip() or 'Family'
        if len(child_name)<=40:break
        print('Enter a name of 1–40 characters.')
    pin=ask_pin()
    data.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(data/'kidoo.db',os.O_CREAT | os.O_EXCL | os.O_WRONLY,0o600)
    os.close(fd)
    init_db(data/'kidoo.db')
    with sqlite3.connect(data/'kidoo.db') as db:
        db.execute("UPDATE profiles SET name=? WHERE id='family'",(child_name,))
    security=SecurityStore(data/'kidoo.db')
    security.create_administrator(name)
    security.set_pin(pin)
    del pin
    print('Parent administrator and PIN saved in the new Kidoo data directory.')
    if input('Connect an existing Jellyfin account now? [y/N] ').strip().lower() in ('y','yes'):
        server=input('Jellyfin server URL: ').strip()
        username=input('Jellyfin username: ').strip()
        if server.lower().startswith('http://'):
            print('Warning: HTTP sends the Jellyfin password and traffic unencrypted between Kidoo and Jellyfin. Use HTTPS or a trusted network.')
        password=getpass.getpass('Jellyfin password: ')
        try:
            mapping=jellyfin.authenticate(server,username,password)
            jellyfin.save_config(data,'family',mapping)
            print('Jellyfin connected to the first child profile.')
        except (jellyfin.JellyfinError,ValueError,OSError) as error:
            print('Jellyfin was not connected:',error)
            print('After Kidoo starts, open the parent dashboard and use Connect Jellyfin to try again.')
        finally: del password
    command=[sys.executable,str(Path(__file__).resolve().parent/'kidoo_server.py'),
             '--media-dir',str(media),'--data-dir',str(data),'--host','0.0.0.0','--port',str(args.port),
             '--protected-mode','--tls-cert',str(cert),'--tls-key',str(key)]
    launcher=data/'start-kidoo.sh'
    launcher_created=False
    try:
        launcher_fd=os.open(launcher,os.O_CREAT | os.O_EXCL | os.O_WRONLY,0o700)
        os.fchmod(launcher_fd,0o700)
        with os.fdopen(launcher_fd,'w') as script:
            script.write('#!/bin/sh\nexec '+shlex.join(command)+'\n')
        launcher_created=True
        print('\nTo start Kidoo again, run: '+shlex.quote(str(launcher)))
    except FileExistsError:
        print('\nAn existing start script was not changed. Start Kidoo with:')
        print(shlex.join(command))
    print(f'\nWhen Kidoo is running, open https://{hostname}:{args.port}/dashboard and sign in with the parent name and PIN.')
    print('Follow the parent dashboard checklist to add and approve videos, then pair a TV or child browser.')
    checker=Path(__file__).resolve().parent/'connection_check.py'
    print('To check this computer can verify the protected server after it starts, run:')
    print(shlex.join([sys.executable,str(checker),f'https://{hostname}:{args.port}','--connect-ip','127.0.0.1']))
    print('This checks only this computer; test trust and reachability separately on every device.')
    print('Install and open the Kidoo TV app first. Pairing codes expire after five minutes, and Kidoo must be running when the code is entered.')
    print('For less waiting, start Kidoo and create the code in the parent dashboard when the TV is ready.')
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
    if launcher_created and input('Start Kidoo automatically when this user logs in? [y/N] ').strip().lower() in ('y','yes'):
        try:
            service=autostart.install(launcher)
            print('Automatic startup enabled and Kidoo started: '+str(service))
            print('The computer must be awake and this user logged in; see docs/SETUP.md to stop or remove the service.')
            return 0
        except (OSError, ValueError, RuntimeError) as error:
            print('Automatic startup was not enabled:',error)
            print('You can still start Kidoo using the saved script.')
    if input('Start Kidoo now? [y/N] ').strip().lower() in ('y','yes'):
        return subprocess.call(command)
    return 0


if __name__=='__main__':
    try: sys.exit(main())
    except (KeyboardInterrupt,EOFError): print('\nSetup stopped.');sys.exit(1)
    except (SecurityError,OSError) as error: print('Setup could not finish:',error,file=sys.stderr);sys.exit(1)
