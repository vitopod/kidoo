import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import renew_certificate


class CertificateRenewalTest(unittest.TestCase):
    def test_valid_certificate_needs_no_renewal_or_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory) / 'server.crt'; key = Path(directory) / 'server.key'
            cert.write_text('certificate'); key.write_text('key')
            with patch('renew_certificate.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as process:
                self.assertFalse(renew_certificate.maintain('example.test', cert, key, 'kidoo.service'))
                self.assertEqual(process.call_count, 2)
                self.assertFalse(any(call.args[0][0] in ('tailscale', 'systemctl') for call in process.call_args_list))

    def test_failed_issuance_does_not_replace_existing_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory) / 'server.crt'; key = Path(directory) / 'server.key'
            cert.write_text('original cert'); key.write_text('original key')
            with patch('renew_certificate.run', side_effect=subprocess.CalledProcessError(1, 'tailscale')):
                with self.assertRaises(subprocess.CalledProcessError):
                    renew_certificate.maintain('example.test', cert, key, '', force=True)
            self.assertEqual(cert.read_text(), 'original cert')
            self.assertEqual(key.read_text(), 'original key')

    def test_cached_certificate_does_not_restart_service(self):
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory) / 'server.crt'; key = Path(directory) / 'server.key'
            cert.write_text('same cert'); key.write_text('same key')
            calls=[]
            def fake(args, **kwargs):
                calls.append(args)
                if args[0]=='tailscale':
                    Path(args[args.index('--cert-file')+1]).write_text('same cert')
                    Path(args[args.index('--key-file')+1]).write_text('same key')
                return subprocess.CompletedProcess(args,0,stdout=b'public')
            with patch('renew_certificate.run',side_effect=fake):
                self.assertFalse(renew_certificate.maintain('example.test',cert,key,'kidoo.service',force=True))
            self.assertFalse(any(args[0]=='systemctl' for args in calls))


if __name__ == '__main__': unittest.main()
