"""Exercise the shipped root service/legacy cutover ONLY on a fresh GitHub-hosted Mac.

No fan set/auto requests, SMAppService approval, or signed production identities.
The test owns its fixture paths, refuses collisions, and removes only those paths.
"""
import json
import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time


def hosted_mac():
    return (sys.platform == 'darwin' and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
            and os.environ.get('RUNNER_OS') == 'macOS' and os.geteuid() != 0)


def previous_release(work):
    # Immutable, signed v0.4.5 fixture; no anonymous GitHub API quota needed.
    name = "AgenticFan-0.4.5-arm64.dmg"
    target = work / name
    digest = "f1b5243481a5f56e478d0df43c388cc32423029da513b9cc974c4141512a18f9"
    subprocess.run(["curl","-q","--fail","--silent","--show-error","--location","--proto","=https",
                    "--retry","2","--max-time","90","--output",str(target),
                    "https://github.com/timmyagentic/agentic-fan/releases/download/v0.4.5/" + name],check=True)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    target.with_suffix(".dmg.sha256").write_text(f"{digest}  {name}\n")
    return target


def seed_published_app(dmg, work, user):
    """Verify/replace the genuine artifact even where its embedded Python cannot start."""
    mount = work / 'published'; mount.mkdir()
    app = user / 'Applications/AgenticFan.app'; app.parent.mkdir()
    subprocess.run(['hdiutil', 'attach', '-quiet', '-readonly', '-nobrowse', '-mountpoint', mount, dmg], check=True)
    try:
        subprocess.run(['/usr/bin/ditto', mount/'AgenticFan.app', app], check=True)
    finally:
        subprocess.run(['hdiutil', 'detach', mount], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app], check=True)
    assert plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']=='0.4.5'
    cli = app/'Contents/MacOS/agenticfan'
    link = user/'.local/bin/agenticfan'; link.parent.mkdir(parents=True); link.symlink_to(cli)
    return hashlib.sha256(cli.read_bytes()).digest()


def legacy_app_for_vm(work, published):
    cli = published/'Contents/MacOS/agenticfan'
    version = subprocess.run([cli,'--version'], capture_output=True, text=True)
    if version.returncode == 0 and version.stdout.strip() == '0.4.5':
        return published
    # The pinned official DMG links its Python to macOS 27 (_dup3 is absent
    # on macOS 15). Never substitute a stub daemon. Rebuild the exact released
    # CLI/daemon with its own spec and the VM's compatible Python instead.
    if '_dup3' not in version.stderr or 'newer than running OS' not in version.stderr:
        raise RuntimeError('Unexpected error starting the published legacy CLI: '+version.stderr)
    commit = '3d73cce04dfec87d134a4fdee497dbd5f0bbbf41'
    src = work/'legacy-source'
    subprocess.run(['git', 'init', '-q', src],check=True)
    subprocess.run(['git','-C',src,'fetch','-q','--depth=1','https://github.com/timmyagentic/agentic-fan.git',commit],check=True)
    subprocess.run(['git','-C',src,'checkout','-q','--detach','FETCH_HEAD'],check=True)
    assert subprocess.check_output(['git','-C',src,'rev-parse','HEAD'],text=True).strip()==commit
    products = work/'legacy-products'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
                    '--distpath', products, '--workpath', work/'legacy-build',
                    src/'packaging/macos/agenticfan.spec'], cwd=src, check=True)
    result = products/'AgenticFan.app'
    assert subprocess.check_output([result/'Contents/MacOS/agenticfan','--version'],text=True).strip()=='0.4.5'
    print(f'Legacy runtime: exact v0.4.5 source {commit} rebuilt for this VM (published Python needs macOS 27)')
    return result


def exercise(app, previous_app):
    if not hosted_mac():
        raise RuntimeError('Requires a disposable GitHub-hosted macOS VM')
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from agenticfan import helper
    from agenticfan.helper_install import rpc

    label = 'io.github.timmyagentic.agenticfan.ci-service'
    vm_job = Path('/Library/LaunchDaemons') / (label + '.plist')
    runtime = Path('/private/var/run/agenticfan')
    owned = [helper.BASE, helper.CACHE, helper.SOCKET_DIRECTORY, runtime, Path('/var/run/agenticfan-helper'), helper.LEGACY_PLIST, vm_job]

    def admin(*args, check=True):
        return subprocess.run(['/usr/bin/sudo', '-n', *map(str, args)], check=check, capture_output=True, timeout=30)
    for path in owned:
        if os.path.lexists(path):
            raise RuntimeError('Refusing to use pre-existing service paths')
    for job in (label, helper.LEGACY_LABEL, helper.LABEL):
        if admin('/bin/launchctl','print','system/' + job,check=False).returncode not in (3,113):
            raise RuntimeError('Refusing to change pre-existing launchd jobs')

    bootstrap = app / 'Contents/MacOS/AgenticFanService'
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        def install_file(path, target, mode='0644'):
            admin('/usr/bin/install','-o','root','-g','wheel','-m',mode,path,target)
        try:
            admin('/bin/mkdir','-p',helper.LEGACY_EXECUTABLE.parent)
            grant = json.dumps({'authorized_uid':os.getuid(),'version':'0.4.5'}).encode()
            (tmp/'grant').write_bytes(grant); install_file(tmp/'grant', helper.CONFIG, '0600')
            # The actual 0.4.5 root daemon starts idle and accepts status only
            # during this test. No fan command is issued to either generation.
            old_app = helper.LEGACY_EXECUTABLE.parents[2]
            admin('/usr/bin/ditto',previous_app,old_app)
            admin('/usr/sbin/chown','-Rh','root:wheel',old_app)
            admin('/bin/chmod','-R','go-w',old_app)
            (tmp/'v1.plist').write_bytes(plistlib.dumps({'Label':helper.LEGACY_LABEL,
                'ProgramArguments':[str(helper.LEGACY_EXECUTABLE),'--internal-helper'],'RunAtLoad':True}))
            install_file(tmp/'v1.plist',helper.LEGACY_PLIST)
            admin('/bin/launchctl','bootstrap','system',helper.LEGACY_PLIST)
            deadline = time.monotonic()+15
            while time.monotonic()<deadline:
                previous = json.loads(subprocess.check_output([str(previous_app/'Contents/MacOS/agenticfan'),
                                                              'helper','status'],text=True))['data']
                if previous.get('running'): break
                time.sleep(.2)
            assert previous.get('compatible') and previous['version']=='0.4.5', previous
            # Use the exact DMG bootstrap and worker under a disposable label.
            # Real SMAppService registration/approval is a separate acceptance.
            (tmp/'v2.plist').write_bytes(plistlib.dumps({'Label':label,'ProgramArguments':[str(bootstrap)],
                'UserName':'root','RunAtLoad':True,'AbandonProcessGroup':True,
                'StandardErrorPath':str(tmp/'service.log')}))
            install_file(tmp/'v2.plist',vm_job)
            admin('/bin/launchctl','bootstrap','system',vm_job)
            deadline = time.monotonic()+25
            while time.monotonic()<deadline:
                status=helper.status()
                if status.get('running'): break
                time.sleep(.2)
            if status.get('state') != 'migration_required':
                log = admin('/usr/bin/tail','-n','15',tmp/'service.log',check=False)
                raise AssertionError(f'Packaged service did not start: {status}; {log.stdout.decode(errors="replace")}')
            assert rpc('migrate')['state']=='ready'
            assert admin('/usr/bin/cmp',tmp/'grant',helper.CONFIG,check=False).returncode==0
            assert not helper.LEGACY_PLIST.exists() and (helper.BASE/'legacy-launchd.plist').is_file()
            assert admin('/bin/launchctl','print','system/'+helper.LEGACY_LABEL,check=False).returncode in (3,113)
            assert rpc('drain')['state']=='updating'
            assert rpc('resume')['state']=='ready'
            info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
            assert helper.status()['service_build']==info['AgenticFanServiceBuild']
            print('PASS: packaged root service, kernel-authenticated IPC, legacy launchd cutover, retained UID, drain/resume (no fan writes)')
        finally:
            for job in (label,helper.LEGACY_LABEL):
                admin('/bin/launchctl','bootout','system/'+job,check=False)
            admin('/bin/rm','-rf',*owned)
