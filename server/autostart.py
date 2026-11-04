"""Opt-in, per-user startup for an existing protected Kidoo launcher."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys


def systemd_quote(value):
    value=str(value)
    if any(c in value for c in '\n\r\0'): raise ValueError('Invalid launcher path')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'


def install(launcher, *, platform=None, home=None, run=subprocess.run):
    launcher=Path(launcher)
    if not launcher.is_file() or launcher.is_symlink(): raise ValueError('Protected start script not found')
    launcher=launcher.resolve()
    platform=platform or sys.platform
    home=Path(home or Path.home())
    if platform=='darwin':
        target=home/'Library'/'LaunchAgents'/'org.kidoo.server.plist'
        content=plistlib.dumps({'Label':'org.kidoo.server','ProgramArguments':[str(launcher)],'RunAtLoad':True,'KeepAlive':True})
        commands=[['launchctl','bootstrap',f'gui/{os.getuid()}',str(target)]]
    elif platform.startswith('linux'):
        target=home/'.config'/'systemd'/'user'/'kidoo.service'
        content=('[Unit]\nDescription=Kidoo family media server\n\n[Service]\nType=simple\nExecStart='+systemd_quote(launcher)+'\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n').encode()
        commands=[['systemctl','--user','daemon-reload'],['systemctl','--user','enable','--now','kidoo.service']]
    else:
        raise ValueError('Automatic startup is supported on macOS and Linux with systemd user services')
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(target,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as output: output.write(content)
    try:
        for command in commands: run(command,check=True)
    except (OSError,subprocess.CalledProcessError) as error:
        raise RuntimeError(f'Could not enable automatic startup. The new file {target} remains for inspection; no existing service was replaced.') from error
    return target
