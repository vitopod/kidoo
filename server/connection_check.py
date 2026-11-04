#!/usr/bin/env python3
"""Check a protected Kidoo HTTPS connection from this device, without signing in."""
import argparse
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit


class ConnectionCheckError(ValueError):
    pass


def check(address, connect_ip=None, *, context=None, timeout=5):
    """Verify the TLS chain, hostname and public Kidoo protected-mode endpoint.

    A private IP override changes only the network route; certificate validation,
    TLS SNI and the HTTP Host header retain the original hostname.
    """
    try:
        parts=urlsplit(address)
        port=parts.port or 443
        if (parts.scheme!='https' or not parts.hostname or parts.username or parts.password
                or parts.path not in ('','/') or parts.query or parts.fragment):
            raise ValueError()
        host=parts.hostname
        if connect_ip:
            target=ipaddress.ip_address(connect_ip)
            shared_tailnet=isinstance(target,ipaddress.IPv4Address) and target in ipaddress.ip_network('100.64.0.0/10')
            if not (target.is_private or target.is_loopback or target.is_link_local or shared_tailnet):
                raise ValueError()
            target=str(target)
        else: target=host
    except (ValueError,TypeError):
        raise ConnectionCheckError('Use an HTTPS server address without a path or credentials; the optional connection IP must be private.') from None
    context=context or ssl.create_default_context()
    if context.verify_mode!=ssl.CERT_REQUIRED or not context.check_hostname:
        raise ConnectionCheckError('Certificate and hostname verification are required.')
    try:
        with socket.create_connection((target,port),timeout=timeout) as raw:
            with context.wrap_socket(raw,server_hostname=host) as secure:
                authority=f'[{host}]' if ':' in host else host
                if port!=443:authority+=f':{port}'
                secure.sendall(f'GET /api/tv/info HTTP/1.1\r\nHost: {authority}\r\nAccept: application/json\r\nConnection: close\r\n\r\n'.encode('ascii'))
                response=http.client.HTTPResponse(secure);response.begin()
                if response.status!=200:raise ConnectionCheckError(f'The server returned HTTP {response.status}; check the address and port.')
                body=response.read(65537)
                if len(body)>65536:raise ConnectionCheckError('The server response was too large.')
                try: info=json.loads(body)
                except (ValueError,UnicodeDecodeError):raise ConnectionCheckError('This address did not return Kidoo information.') from None
                if not isinstance(info,dict) or info.get('product')!='Kidoo':raise ConnectionCheckError('This address did not identify itself as Kidoo.')
                if info.get('secureParentRoles') is not True:raise ConnectionCheckError('Kidoo is reachable, but protected mode is not enabled.')
                return {'ok':True,'product':'Kidoo','protectedMode':True}
    except ssl.SSLCertVerificationError as error:
        raise ConnectionCheckError('The certificate is not trusted, has expired, or does not match the hostname on this device. Check its trust, dates and address.') from error
    except ssl.SSLError as error:
        raise ConnectionCheckError('The secure connection failed. Check that HTTPS is enabled on this port.') from error
    except (socket.timeout,TimeoutError) as error:
        raise ConnectionCheckError('The connection timed out. Check that Kidoo is running and reachable from this device.') from error
    except (OSError,http.client.HTTPException,UnicodeEncodeError) as error:
        raise ConnectionCheckError('Could not reach Kidoo. Check the hostname, network, port and server status.') from error


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('address',help='Kidoo HTTPS address, e.g. https://kidoo.example:8765')
    parser.add_argument('--connect-ip',help='Optional private IP to connect to while validating the HTTPS hostname')
    args=parser.parse_args(argv)
    try:check(args.address,args.connect_ip)
    except ConnectionCheckError as error:
        print('Connection check failed:',error)
        return 1
    print('This device reached Kidoo over HTTPS, verified its certificate and hostname, and found protected mode enabled.')
    print('Other devices, network routes and future certificate renewals must be checked separately.')
    return 0


if __name__=='__main__':raise SystemExit(main())
