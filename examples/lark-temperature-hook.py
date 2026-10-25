#!/usr/bin/env python3
"""Register this fixed executable beside a lark-temperature-hook.json settings file."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    event = json.load(sys.stdin)
    if event.get('event') != 'rule_triggered' or event.get('simulated') is not False:
        return 0  # Demo events never send real messages.
    settings = json.loads(Path(__file__).with_suffix('.json').read_text())
    cli, identity = settings['cli'], settings['identity']
    targets = [(key, settings[key]) for key in ('chat_id', 'user_id') if settings.get(key)]
    if not Path(cli).is_absolute() or identity not in ('user', 'bot') or len(targets) != 1:
        raise ValueError('Configure an absolute CLI path, explicit identity and exactly one recipient.')
    if type(settings.get('dry_run', True)) is not bool:
        raise ValueError('dry_run must be boolean.')
    event_id = event['event_id']
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 50:
        raise ValueError('Invalid event ID.')
    rule = event['rule']
    message = (f"AgenticFan 温度规则触发：{rule['id']}\n"
               f"{rule['sensor_group'].upper()} 最高有效测点 {event['temperature_celsius']:.1f}°C，"
               f"达到 {rule['trigger_celsius']}°C 并持续 {rule.get('trigger_seconds', 0)} 秒。\n"
               f"请求目标：{event['target_rpm']} RPM；控扇状态：{event['control_status']}。\n"
               f"实际转速确认：{'已确认' if event['control_confirmed'] else '尚未确认'}。")
    kind, recipient = targets[0]
    args = [cli, 'im', '+messages-send', '--as', identity, '--' + kind.replace('_', '-'),
            recipient, '--text', message, '--idempotency-key', event_id]
    if settings.get('dry_run', True): args.append('--dry-run')
    result = subprocess.run(args, capture_output=True, text=True, timeout=8)
    # Do not relay arbitrary CLI output (including authentication material) into app logs.
    if result.returncode:
        print(f'lark-cli failed with exit {result.returncode}', file=sys.stderr)
        return 1
    if not settings.get('dry_run', True) and json.loads(result.stdout).get('ok') is not True:
        print('lark-cli did not confirm success', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f'Lark hook failed: {type(exc).__name__}', file=sys.stderr)
        sys.exit(1)
