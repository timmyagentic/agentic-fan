"""Hook script documents shared by the JSON CLI and native editor.

Reading and validation never initialize files or run a user's hook/interpreter.
The byte revision is independent of the temperature-rule JSON revision.
"""
import ast
from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import warnings as python_warnings

from .errors import AgenticFanError, invalid
from .runtime import lock

MAX_BYTES = 256 * 1024
TEMPLATE = ('#!/bin/sh\n# 在这里编写温度规则触发后要执行的代码。\n'
            '# 事件 JSON 通过标准输入传入；默认不执行任何动作。\n\n')
EVENT_EXAMPLE = {
    'version': 1, 'event': 'rule_triggered', 'event_id': '00000000-0000-4000-8000-000000000000',
    'timestamp': 0, 'simulated': True,
    'rule': {'id': 'cpu-hot', 'enabled': True, 'sensor_group': 'cpu', 'trigger_celsius': 100,
             'trigger_seconds': 10, 'release_celsius': 90, 'release_seconds': 20, 'target_rpm': 'max'},
    'temperature_celsius': 101.2, 'fan': 0, 'target_rpm': 4900,
    'control_status': 'active', 'control_confirmed': False,
}


def _issue(code, message):
    return {'code': code, 'message': message}


def default_path(config_path):
    return Path(config_path).absolute().parent / 'hooks/threshold-hook.sh'


def inspect_script(path, allow_new=False):
    path = Path(path)
    result = {'path': str(path), 'exists': False, 'editable': False, 'executable': False,
              'revision': 'missing', 'content': None, 'permissions': None, 'issue': None}
    try:
        info = path.lstat()
        result.update(exists=True, revision=None)
        if not stat.S_ISREG(info.st_mode):
            result['issue'] = _issue('NOT_REGULAR_FILE', 'Hook editing requires a regular file, not a link or directory.')
            return result
        result.update(permissions=stat.S_IMODE(info.st_mode), executable=os.access(path, os.X_OK))
        if info.st_size > MAX_BYTES:
            result['issue'] = _issue('SCRIPT_TOO_LARGE', 'Editable scripts are limited to 256 KiB.')
            return result
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError('The script changed file type while being read.')
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            result['issue'] = _issue('SCRIPT_TOO_LARGE', 'Editable scripts are limited to 256 KiB.')
            return result
        result['revision'] = hashlib.sha256(data).hexdigest()
        try:
            text = data.decode('utf-8')
            if '\0' in text:
                raise ValueError('NUL')
        except (UnicodeError, ValueError):
            result['issue'] = _issue('NOT_TEXT_SCRIPT', 'The existing program is not a UTF-8 text script; it has not been changed.')
            return result
        result.update(editable=True, content=text)
    except FileNotFoundError:
        result.update(exists=False, revision='missing', permissions=None, executable=False,
                      editable=allow_new, content=TEMPLATE if allow_new else None,
                      issue=None if allow_new else _issue('SCRIPT_MISSING', 'The configured script does not exist.'))
    except OSError as exc:
        result['issue'] = _issue('SCRIPT_READ_FAILED', str(exc))
    return result


def observed_revision(path):
    """An observation of a bounded regular file, not an immutable execution snapshot."""
    result = inspect_script(path)
    return result['revision'] if result['exists'] else None


def normalize(content):
    if len(content) > MAX_BYTES:
        raise invalid('Hook script exceeds 256 KiB.')
    try:
        text = content.decode('utf-8')
    except UnicodeError:
        raise invalid('Hook script must be UTF-8 text.') from None
    if '\0' in text:
        raise invalid('Hook script must not contain NUL bytes.')
    if not text.startswith('#!'):
        text = '#!/bin/sh\n' + text
    if len(text.encode()) > MAX_BYTES:
        raise invalid('Normalized Hook script exceeds 256 KiB.')
    return text


def _check(text, path, scope, executable=True, file_issue=None):
    issues, warnings = [], []
    syntax = {'status': 'unsupported', 'checker': None}
    interpreter = {'path': None, 'resolved_path': None, 'environment_dependent': False}
    if file_issue:
        issues.append(file_issue)
    if scope == 'file' and not executable and not file_issue:
        issues.append(_issue('NOT_EXECUTABLE', 'The script is not executable; save it with hook apply to set owner execute permission.'))
    if text is not None:
        first = text.partition('\n')[0]
        # The kernel does not perform shell quoting on the interpreter path.
        words = re.split(r'[ \t]+', first[2:].lstrip(' \t'), maxsplit=1) if first.startswith('#!') and '\r' not in first else []
        if not words or not Path(words[0]).is_absolute():
            issues.append(_issue('INVALID_INTERPRETER', 'A shebang with an absolute interpreter path is required.'))
        else:
            interpreter['path'] = words[0]
            resolved = words[0]
            if not Path(resolved).is_file() or not os.access(resolved, os.X_OK):
                issues.append(_issue('INTERPRETER_UNAVAILABLE', 'The shebang interpreter is missing or not executable.'))
                resolved = None
            elif words[0] == '/usr/bin/env':
                interpreter['environment_dependent'] = True
                argument = words[1].strip(' \t') if len(words) > 1 else ''
                if argument.startswith(('-S ', '-S\t')):
                    try:
                        arguments = shlex.split(argument[2:].strip())
                    except ValueError:
                        arguments = []
                elif re.search(r'[ \t]', argument):
                    arguments = []
                    issues.append(_issue('INVALID_ENV_SHEBANG', 'Use env -S when supplying more than one shebang argument.'))
                else:
                    arguments = [argument] if argument else []
                if not arguments or arguments == ['-S']:
                    resolved = None
                    issues.append(_issue('INVALID_ENV_SHEBANG', 'The env shebang must name an interpreter.'))
                elif not arguments[0].startswith('-') and '=' not in arguments[0]:
                    resolved = shutil.which(arguments[0])
                    if not resolved:
                        issues.append(_issue('INTERPRETER_UNAVAILABLE', 'The requested interpreter was not found on this process PATH.'))
                else:
                    resolved = None
                    warnings.append(_issue('ENV_FLAGS_UNCHECKED', 'This env shebang could not be resolved statically.'))
                warnings.append(_issue('ENVIRONMENT_DEPENDENT', 'Interpreter resolution uses this process PATH; prefer an absolute interpreter path for login startup.'))
            elif len(words) > 1:
                warnings.append(_issue('INTERPRETER_ARGUMENTS_UNCHECKED', 'Interpreter arguments are not executed or validated by the syntax checker.'))
            interpreter['resolved_path'] = resolved
            if resolved in ('/bin/sh', '/bin/bash', '/usr/bin/sh', '/usr/bin/bash'):
                syntax['checker'] = resolved + ' -n'
                try:
                    # Do not use the script's arguments or caller-provided shell startup files.
                    checked = subprocess.run([resolved, '-n'], input=text, text=True, capture_output=True,
                                             timeout=3, env={'PATH': '/usr/bin:/bin', 'BASH_ENV': '/dev/null', 'ENV': '/dev/null', 'LC_ALL': 'C'})
                    syntax['status'] = 'passed' if checked.returncode == 0 else 'failed'
                    if checked.returncode:
                        issues.append(_issue('SYNTAX_ERROR', checked.stderr[:4096].strip() or 'Shell syntax check failed.'))
                except (OSError, subprocess.TimeoutExpired) as exc:
                    syntax['status'] = 'unavailable'
                    issues.append(_issue('SYNTAX_CHECK_FAILED', str(exc)))
            elif resolved and re.fullmatch(r'python(?:3(?:\.\d+)?)?', Path(resolved).name):
                syntax['checker'] = f'Python {sys.version_info.major}.{sys.version_info.minor} ast.parse'
                try:
                    with python_warnings.catch_warnings(record=True) as caught:
                        python_warnings.simplefilter('always')
                        ast.parse(text, filename=str(path))
                    warnings.extend(_issue('SYNTAX_WARNING', str(item.message)[:1024]) for item in caught)
                    syntax['status'] = 'passed'
                except (SyntaxError, ValueError) as exc:
                    syntax['status'] = 'failed'
                    issues.append(_issue('SYNTAX_ERROR', str(exc)))
    if syntax['status'] == 'unsupported':
        warnings.append(_issue('SYNTAX_UNCHECKED', 'No trusted syntax checker is available for this interpreter. The program was not executed.'))
    return {'valid': not issues, 'scope': scope, 'path': str(path), 'interpreter': interpreter,
            'syntax': syntax, 'issues': issues, 'warnings': warnings, 'executed': False}


def _selection(config_path, use_default):
    from . import rules
    document = rules.load(Path(config_path).absolute())
    default = default_path(document.path)
    configured = document.config.get('hook')
    path = default if use_default or not configured else Path(configured['program'])
    return document, path, default


def _execution(document, script):
    from . import rules
    runtime = rules.status(document.path)
    hook = runtime.get('hook') or {}
    last = hook.get('last_result')
    comparison = None
    if last and last.get('script_revision') and script['exists'] and script['revision']:
        comparison = (last.get('program') == script['path'] and
                      last['script_revision'] == script['revision'] == last.get('script_revision_after'))
    return {'runner_running': runtime.get('running', False), 'runner_status': runtime.get('status', 'not_running'),
            'settings_loaded': bool(runtime.get('running') and runtime.get('loaded_generation') == document.generation),
            'running': bool(runtime.get('running') and hook.get('running')), 'queued': hook.get('queued', 0), 'dropped': hook.get('dropped', 0),
            'last_result': last, 'last_observed_revision_matches_current': comparison,
            'revision_evidence': 'observed_before_and_after_execution_not_an_immutable_snapshot'}


def show(config_path, default=False, include_content=True):
    document, path, fallback = _selection(config_path, default)
    script = inspect_script(path, allow_new=path == fallback)
    script['default_path'] = str(fallback)
    configured = document.config.get('hook')
    result = {'script': script, 'settings': {
        'enabled': configured is not None, 'timeout_seconds': (configured or {}).get('timeout_seconds', 10),
        'rules_enabled': document.config['enabled'], 'rules_path': str(document.path), 'rules_revision': document.revision,
        'configured_program': configured['program'] if configured else None,
        'selected_program_is_configured': bool(configured and configured['program'] == str(path)),
    }, 'execution': _execution(document, script)}
    if include_content:
        result['event_example'] = deepcopy(EVENT_EXAMPLE)
    else:
        script.pop('content')
    return result


def validate(config_path, content=None, default=False):
    _, path, fallback = _selection(config_path, default)
    if content is not None:
        text = normalize(content)
        return {**_check(text, path, 'draft'), 'revision': hashlib.sha256(text.encode()).hexdigest(),
                'normalized': text.encode() != content}
    script = inspect_script(path, allow_new=path == fallback)
    issue = script['issue']
    if not script['exists']:
        issue = _issue('SCRIPT_MISSING', 'The script has not been saved or does not exist.')
    return {**_check(script['content'] if script['exists'] else None, path, 'file', script['executable'], issue),
            'revision': script['revision']}


def status(config_path, default=False):
    result = show(config_path, default, include_content=False)
    result['validation'] = validate(config_path, default=default)
    if result['validation']['revision'] != result['script']['revision']:
        result['validation']['valid'] = False
        result['validation']['issues'].append(_issue('SCRIPT_CHANGED_DURING_CHECK', 'The script changed while status was being read; refresh before relying on this report.'))
    return result


def _write_script(path, text, expected):
    current = inspect_script(path, allow_new=True)
    if current['revision'] != expected:
        raise AgenticFanError('HOOK_CONFLICT', 'Hook script changed. Reload and merge before saving.', 6,
                              expected_revision=expected, current_revision=current['revision'])
    if not current['editable']:
        raise AgenticFanError('HOOK_NOT_EDITABLE', 'The existing file cannot be edited as a script.', 2, issue=current['issue'])
    data = text.encode()
    mode = ((current['permissions'] if current['permissions'] is not None else 0o700) & 0o777) | 0o100
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data); stream.flush(); os.fchmod(stream.fileno(), mode); os.fsync(stream.fileno())
        if inspect_script(path, allow_new=True)['revision'] != expected:
            raise AgenticFanError('HOOK_CONFLICT', 'Hook script changed. Reload before saving.', 6)
        if expected == 'missing':
            try:
                os.link(temporary, path)
            except FileExistsError:
                raise AgenticFanError('HOOK_CONFLICT', 'Another writer created the script. Reload before saving.', 6) from None
        else:
            os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


def apply(config_path, content=None, *, if_revision=None, if_rules_revision=None,
          enabled=None, timeout_seconds=None, default=False, wait=0):
    from . import rules
    if enabled is not None and type(enabled) is not bool:
        raise invalid('Hook enablement must be an explicit boolean.')
    if not if_rules_revision or ((content is not None or enabled is True) and not if_revision):
        raise invalid('hook apply requires --if-rules-revision; script writes and enabling also require --if-revision from hook show.')
    if content is None and enabled is None and timeout_seconds is None:
        raise invalid('Provide script content or an explicit Hook setting to apply.')
    if not math.isfinite(wait) or not 0 <= wait <= 10:
        raise invalid('--wait must be finite and between 0 and 10 seconds.')
    if timeout_seconds is not None and (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not .1 <= timeout_seconds <= 60):
        raise invalid('Hook timeout must be between 0.1 and 60 seconds.')
    document, path, _ = _selection(config_path, default)
    if path.resolve() in (document.path.resolve(), rules.state_path(document.path).resolve()):
        raise invalid('The Hook script must not overwrite its rules or runtime-state file.')
    if document.revision != if_rules_revision:
        raise AgenticFanError('CONFIG_CONFLICT', 'Rules changed. Reload hook show before saving.', 6)
    if if_revision is not None and inspect_script(path, allow_new=True)['revision'] != if_revision:
        raise AgenticFanError('HOOK_CONFLICT', 'Hook script changed. Reload before saving or enabling.', 6)
    text = normalize(content) if content is not None else None
    if text is not None:
        current = inspect_script(path, allow_new=True)
        if current['revision'] != if_revision:
            raise AgenticFanError('HOOK_CONFLICT', 'Hook script changed. Reload before saving.', 6)
        if not current['editable']:
            raise AgenticFanError('HOOK_NOT_EDITABLE', 'The existing file cannot be edited as a script.', 2, issue=current['issue'])
        checked = _check(text, path, 'draft')
    else:
        checked = validate(config_path, default=default) if enabled is True else None
    if checked and not checked['valid']:
        raise AgenticFanError('HOOK_VALIDATION_FAILED', 'Hook validation failed; no changes were saved.', 2, validation=checked)
    config = deepcopy(document.config)
    if enabled is False:
        config['hook'] = None
    elif enabled is True:
        config['hook'] = {'program': str(path), 'timeout_seconds': timeout_seconds if timeout_seconds is not None else (config.get('hook') or {}).get('timeout_seconds', 10)}
    elif timeout_seconds is not None:
        if not config.get('hook') or config['hook']['program'] != str(path):
            raise invalid('Changing the timeout of an unconfigured script requires --enable.')
        config['hook']['timeout_seconds'] = timeout_seconds
    rules.validate(config)
    settings_changed = config != document.config
    script_saved = False
    settings_saved = False
    written_revision = None
    document.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        # All cooperating clients lock settings first, then the selected script.
        with lock(document.path.parent, '.' + document.path.name + '.writer.lock', blocking=False):
            if rules.load(document.path).revision != if_rules_revision:
                raise AgenticFanError('CONFIG_CONFLICT', 'Rules changed. Reload before saving.', 6)
            if if_revision is not None and inspect_script(path, allow_new=True)['revision'] != if_revision:
                raise AgenticFanError('HOOK_CONFLICT', 'Hook script changed. Reload before saving or enabling.', 6)
            if text is not None:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with lock(path.parent, '.' + path.name + '.hook-writer.lock', blocking=False):
                    written_revision = _write_script(path, text, if_revision)
                    script_saved = True
            if settings_changed:
                rules.atomic_json(document.path, config, durable=True)
                settings_saved = True
            saved_document = rules.load(document.path)
        loaded = rules.saved_result(saved_document, wait)
        return {'saved': True, 'script_saved': script_saved, 'script_saved_revision': written_revision,
                'settings_saved': settings_saved, 'rules_loaded': loaded['loaded'],
                'rules_revision': saved_document.revision, 'rules_generation': saved_document.generation,
                'script_effect': 'next_trigger_not_executed_by_apply', 'validation': checked,
                'document': show(config_path, default=default)}
    except (OSError, AgenticFanError) as exc:
        if script_saved or settings_saved:
            raise AgenticFanError('HOOK_APPLY_PARTIAL', 'Some Hook changes were saved, but the operation did not complete.', 5,
                                  script_saved=script_saved, script_saved_revision=written_revision, settings_saved=settings_saved,
                                  path=str(path), reason=str(exc)) from None
        if isinstance(exc, AgenticFanError):
            if exc.code == 'CONTROL_BUSY':
                raise AgenticFanError('HOOK_BUSY', 'Another client is updating Hook settings. Reload and retry.', 6) from None
            raise
        raise AgenticFanError('HOOK_IO_ERROR', 'Unable to save Hook files.', 5, reason=str(exc)) from None
