"""Read certificate expiry for the parent dashboard without exposing file paths."""
import ssl
import time


def read(path, now=None):
    if not path: return {'configured':False}
    try:
        info=ssl._ssl._test_decode_cert(str(path))
        expiry=ssl.cert_time_to_seconds(info['notAfter'])
        now=time.time() if now is None else now
        return {'configured':True,'expiresAt':expiry,'expiringSoon':expiry-now <= 30*86400,'expired':expiry<=now}
    except (AttributeError, KeyError, OSError, ssl.SSLError, ValueError, OverflowError):
        return {'configured':True,'unavailable':True}
