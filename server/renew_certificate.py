#!/usr/bin/env python3
"""Renew a Tailscale certificate when near expiry, then restart a protected server."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def maintain(hostname, cert, key, service, *, force=False):
    cert, key = Path(cert).expanduser(), Path(key).expanduser()
    if cert.parent != key.parent: raise ValueError('Certificate and key must share a private directory')
    cert.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not force and cert.is_file() and key.is_file():
        check = subprocess.run(['openssl', 'x509', '-in', str(cert), '-checkend', str(30 * 86400), '-noout'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        host = subprocess.run(['openssl', 'x509', '-in', str(cert), '-checkhost', hostname, '-noout'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if check.returncode == 0 and host.returncode == 0:
            print('Certificate remains valid for more than 30 days.')
            return False
    pending_cert = cert.with_name(cert.name + '.pending')
    pending_key = key.with_name(key.name + '.pending')
    run(['tailscale', 'cert', '--cert-file', str(pending_cert), '--key-file', str(pending_key), hostname])
    os.chmod(pending_cert, 0o600); os.chmod(pending_key, 0o600)
    run(['openssl', 'verify', '-CApath', '/etc/ssl/certs', '-untrusted', str(pending_cert), str(pending_cert)], stdout=subprocess.DEVNULL)
    run(['openssl', 'x509', '-in', str(pending_cert), '-checkhost', hostname, '-noout'], stdout=subprocess.DEVNULL)
    cert_pub = run(['openssl', 'x509', '-in', str(pending_cert), '-pubkey', '-noout'], stdout=subprocess.PIPE).stdout
    key_pub = run(['openssl', 'pkey', '-in', str(pending_key), '-pubout'], stdout=subprocess.PIPE).stdout
    if cert_pub != key_pub: raise ValueError('Certificate and private key do not match')
    if cert.is_file() and key.is_file() and cert.read_bytes()==pending_cert.read_bytes() and key.read_bytes()==pending_key.read_bytes():
        print('Tailscale returned the current certificate; no restart is needed.')
        return False
    os.replace(pending_key, key); os.replace(pending_cert, cert)
    if service:
        args = ['systemctl', '--user', 'show', service, '--property=ExecStart', '--value']
        status = run(args, stdout=subprocess.PIPE, text=True).stdout
        if '--protected-mode' in status: run(['systemctl', '--user', 'restart', service])
    print('Certificate renewed.')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hostname', required=True)
    parser.add_argument('--cert', required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--service', default='kidoo.service')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    try: maintain(args.hostname, args.cert, args.key, args.service, force=args.force)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'Certificate renewal failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
