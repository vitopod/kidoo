"""Credential primitives for the parent/child authorization boundary.

HTTP callers must require encrypted transport and parent authorization before
issuing or managing credentials and profile approvals.
"""
import hashlib
import hmac
import secrets
import sqlite3
import time


class SecurityError(ValueError):
    pass


class SecurityStore:
    """Store PIN verifiers and revocable, profile-scoped device credentials."""
    def __init__(self, db_path, now=time.time):
        self.db_path = str(db_path)
        self.now = now
        with sqlite3.connect(self.db_path) as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS security_administrators (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
                password_verifier BLOB, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS security_pin (
                id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB NOT NULL, verifier BLOB NOT NULL,
                failures INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS security_pairing (
                digest BLOB PRIMARY KEY, profile_id TEXT NOT NULL, label TEXT NOT NULL,
                expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS security_devices (
                id TEXT PRIMARY KEY, label TEXT NOT NULL, profile_id TEXT NOT NULL,
                credential_hash BLOB NOT NULL UNIQUE, created REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS security_profile_approvals (
                profile_id TEXT NOT NULL, kind TEXT NOT NULL, namespace TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL, PRIMARY KEY(profile_id,kind,namespace,item_id));
            CREATE TABLE IF NOT EXISTS security_profile_blocks (
                profile_id TEXT NOT NULL, kind TEXT NOT NULL, namespace TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL, PRIMARY KEY(profile_id,kind,namespace,item_id));
            CREATE TABLE IF NOT EXISTS security_approval_migrations (
                profile_id TEXT PRIMARY KEY, migrated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS security_parent_sessions (
                token_hash BLOB PRIMARY KEY, device_id TEXT NOT NULL, expires REAL NOT NULL);
            ''')

    def create_administrator(self, name):
        """Create an administrator record with no password and no active login flow."""
        if not isinstance(name,str) or not name.strip() or len(name.strip())>40:
            raise SecurityError('Administrator name must be 1–40 characters')
        name=name.strip()
        ident=secrets.token_urlsafe(16)
        try:
            with sqlite3.connect(self.db_path) as db:
                db.execute('INSERT INTO security_administrators(id,name,name_key,password_verifier,created) VALUES (?,?,?,NULL,?)',
                           (ident,name,name.casefold(),self.now()))
        except sqlite3.IntegrityError:
            raise SecurityError('An administrator with this name already exists') from None
        return {'id':ident,'name':name,'role':'administrator','hasPassword':False}

    @staticmethod
    def _digest(value):
        return hashlib.sha256(value.encode('ascii')).digest()

    @staticmethod
    def _pin_hash(pin, salt):
        return hashlib.scrypt(pin.encode('ascii'), salt=salt, n=16384, r=8, p=1, dklen=32)

    @staticmethod
    def _validate_pin(pin):
        if not isinstance(pin, str) or len(pin) < 6 or len(pin) > 12 or not pin.isascii() or not pin.isdigit():
            raise SecurityError('PIN must contain 6–12 digits')

    def set_pin(self, pin):
        """Set/reset only after parent authentication or local server recovery."""
        self._validate_pin(pin)
        salt = secrets.token_bytes(16)
        verifier = self._pin_hash(pin, salt)
        with sqlite3.connect(self.db_path) as db:
            db.execute('INSERT OR REPLACE INTO security_pin(id,salt,verifier,failures,locked_until) VALUES (1,?,?,0,0)', (salt, verifier))
            db.execute('DELETE FROM security_parent_sessions')

    def create_pairing(self, profile_id, label, ttl=300):
        """Issue a single-use pairing code; the caller must authenticate the parent."""
        if not isinstance(profile_id, str) or not profile_id or not isinstance(label, str) or not label.strip() or len(label)>60 or not 1<=ttl<=600:
            raise SecurityError('Invalid pairing request')
        # 128 bits avoids making a stolen database an offline attack on a short code.
        code = secrets.token_urlsafe(16)
        with sqlite3.connect(self.db_path) as db:
            if not db.execute('SELECT 1 FROM profiles WHERE id=?', (profile_id,)).fetchone():
                raise SecurityError('Unknown profile')
            db.execute('DELETE FROM security_pairing WHERE expires<=?', (self.now(),))
            db.execute('INSERT INTO security_pairing(digest,profile_id,label,expires) VALUES (?,?,?,?)',
                       (self._digest(code), profile_id, label.strip(), self.now()+ttl))
        return code

    def redeem_pairing(self, code):
        if not isinstance(code, str) or not code.isascii() or len(code)>128:
            raise SecurityError('Invalid or expired pairing code')
        token, ident = secrets.token_urlsafe(32), secrets.token_urlsafe(16)
        with sqlite3.connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT profile_id,label,expires FROM security_pairing WHERE digest=?', (self._digest(code),)).fetchone()
            if not row or row[2]<=self.now() or not db.execute('SELECT 1 FROM profiles WHERE id=?',(row[0],)).fetchone():
                raise SecurityError('Invalid or expired pairing code')
            db.execute('DELETE FROM security_pairing WHERE digest=?', (self._digest(code),))
            db.execute('INSERT INTO security_devices(id,label,profile_id,credential_hash,created) VALUES (?,?,?,?,?)',
                       (ident,row[1],row[0],self._digest(token),self.now()))
        return {'deviceId':ident,'profileId':row[0],'credential':token}

    def device(self, credential):
        if not isinstance(credential,str) or not credential.isascii() or len(credential)>128: return None
        with sqlite3.connect(self.db_path) as db:
            row=db.execute('SELECT d.id,d.label,d.profile_id FROM security_devices d JOIN profiles p ON p.id=d.profile_id WHERE d.credential_hash=? AND d.revoked=0',(self._digest(credential),)).fetchone()
        return {'id':row[0],'label':row[1],'profileId':row[2]} if row else None

    def _verify_pin_transaction(self, db, pin):
        self._validate_pin(pin)
        row=db.execute('SELECT salt,verifier,failures,locked_until FROM security_pin WHERE id=1').fetchone()
        if not row: raise SecurityError('Parent PIN is not configured')
        if row[3]>self.now(): raise SecurityError('Try again later')
        if not hmac.compare_digest(self._pin_hash(pin,row[0]),row[1]):
            failures=row[2]+1
            db.execute('UPDATE security_pin SET failures=?,locked_until=? WHERE id=1', (failures,self.now()+60 if failures>=5 else 0))
            db.commit()
            raise SecurityError('Invalid PIN')
        db.execute('UPDATE security_pin SET failures=0,locked_until=0 WHERE id=1')

    def authenticate_administrator(self, name, pin):
        if not isinstance(name,str) or not name.strip() or len(name.strip())>40:
            raise SecurityError('Invalid administrator')
        with sqlite3.connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            account=db.execute('SELECT id,password_verifier FROM security_administrators WHERE name_key=?',(name.strip().casefold(),)).fetchone()
            # Apply the same PIN rate limit for unknown names.
            self._verify_pin_transaction(db,pin)
            if not account or account[1] is not None: raise SecurityError('Invalid administrator')
            return account[0]

    def unlock(self, credential, pin, ttl=300):
        if not isinstance(ttl,(int,float)) or not 1<=ttl<=900: raise SecurityError('Invalid session lifetime')
        if not isinstance(credential,str) or not credential.isascii() or len(credential)>128: raise SecurityError('Invalid device')
        with sqlite3.connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            device=db.execute('SELECT d.id FROM security_devices d JOIN profiles p ON p.id=d.profile_id WHERE d.credential_hash=? AND d.revoked=0',(self._digest(credential),)).fetchone()
            if not device: raise SecurityError('Invalid device')
            self._verify_pin_transaction(db,pin)
            token=secrets.token_urlsafe(32)
            db.execute('INSERT INTO security_parent_sessions VALUES (?,?,?)',(self._digest(token),device[0],self.now()+ttl))
        return token

    def is_unlocked(self, credential, session):
        device=self.device(credential)
        if not device or not isinstance(session,str) or not session.isascii() or len(session)>128: return False
        with sqlite3.connect(self.db_path) as db:
            return bool(db.execute('SELECT 1 FROM security_parent_sessions WHERE token_hash=? AND device_id=? AND expires>?',
                                   (self._digest(session),device['id'],self.now())).fetchone())

    def set_approval(self, profile_id, kind, item_id, approved, namespace=''):
        """Set a per-profile approval after the caller verifies parent authority and item validity."""
        if (kind not in ('local','jellyfin') or not isinstance(profile_id,str)
                or not isinstance(item_id,str) or not item_id or len(item_id)>256
                or not isinstance(namespace,str) or len(namespace)>2048 or not isinstance(approved,bool)
                or (kind=='jellyfin') != bool(namespace)):
            raise SecurityError('Invalid approval')
        with sqlite3.connect(self.db_path) as db:
            if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile_id,)).fetchone():
                raise SecurityError('Unknown profile')
            args=(profile_id,kind,namespace,item_id)
            if approved:
                db.execute('DELETE FROM security_profile_blocks WHERE profile_id=? AND kind=? AND namespace=? AND item_id=?',args)
                db.execute('INSERT OR IGNORE INTO security_profile_approvals VALUES (?,?,?,?)',args)
            else:
                db.execute('DELETE FROM security_profile_approvals WHERE profile_id=? AND kind=? AND namespace=? AND item_id=?',args)
                if kind=='jellyfin': db.execute('INSERT OR IGNORE INTO security_profile_blocks VALUES (?,?,?,?)',args)

    def is_approved(self, profile_id, kind, item_id, namespace=''):
        with sqlite3.connect(self.db_path) as db:
            return bool(db.execute('SELECT 1 FROM security_profile_approvals a JOIN profiles p ON p.id=a.profile_id WHERE a.profile_id=? AND a.kind=? AND a.namespace=? AND a.item_id=?',
                                   (profile_id,kind,namespace,item_id)).fetchone())

    def import_legacy_approvals(self, profile_id, jellyfin_server=None):
        """Copy current family approvals once after an explicit parent decision.

        Future changes to the old shared approvals do not propagate. Call this
        only while the migration flow is in control of concurrent changes.
        """
        with sqlite3.connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile_id,)).fetchone():
                raise SecurityError('Unknown profile')
            if db.execute('SELECT 1 FROM security_approval_migrations WHERE profile_id=?',(profile_id,)).fetchone():
                raise SecurityError('Approvals were already imported for this profile')
            db.execute("INSERT OR IGNORE INTO security_profile_approvals SELECT ?, 'local', '', media_id FROM approvals WHERE approved=1",(profile_id,))
            if jellyfin_server:
                db.execute("INSERT OR IGNORE INTO security_profile_approvals SELECT ?, 'jellyfin', server, item_id FROM jellyfin_approvals WHERE server=?",(profile_id,jellyfin_server))
            db.execute('INSERT INTO security_approval_migrations VALUES (?,?)',(profile_id,self.now()))

    def lock(self, credential, session):
        """End one parent session without revoking the paired child device."""
        device=self.device(credential)
        if not device or not isinstance(session,str) or not session.isascii() or len(session)>128: return
        with sqlite3.connect(self.db_path) as db:
            db.execute('DELETE FROM security_parent_sessions WHERE token_hash=? AND device_id=?',
                       (self._digest(session),device['id']))

    def devices(self):
        """Return active device metadata only; the caller must authenticate the parent."""
        with sqlite3.connect(self.db_path) as db:
            rows=db.execute('SELECT id,label,profile_id,created FROM security_devices WHERE revoked=0 ORDER BY created,id').fetchall()
        return [{'id':r[0],'label':r[1],'profileId':r[2],'created':r[3]} for r in rows]

    def assign_device(self, device_id, profile_id):
        """Change a device's profile only after its caller authenticates a parent."""
        if not isinstance(device_id,str) or not isinstance(profile_id,str): raise SecurityError('Invalid assignment')
        with sqlite3.connect(self.db_path) as db:
            if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile_id,)).fetchone(): raise SecurityError('Unknown profile')
            if not db.execute('UPDATE security_devices SET profile_id=? WHERE id=? AND revoked=0',(profile_id,device_id)).rowcount:
                raise SecurityError('Unknown device')

    def revoke_device(self, device_id):
        """Revoke only after parent authentication."""
        with sqlite3.connect(self.db_path) as db:
            db.execute('UPDATE security_devices SET revoked=1 WHERE id=?',(device_id,))
            db.execute('DELETE FROM security_parent_sessions WHERE device_id=?',(device_id,))
