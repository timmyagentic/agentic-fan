"""Dedicated frozen entry: no UI, hooks, config paths, shell or general CLI."""
import json
import sys

from agenticfan import __version__
from agenticfan import helper
from agenticfan.errors import AgenticFanError, invalid
from agenticfan.protocol import emit, failure, success


def main(args):
    if args == ['--service-info']:
        print(json.dumps({'version': __version__, 'protocol': helper.PROTOCOL}))
        return 0
    helper.worker_executable()  # verifies root and the protected runtime path
    if args == ['--internal-helper']:
        return helper.serve()
    if args == ['--internal-guardian']:
        from agenticfan.guardian import main as guardian
        return guardian()
    if args[:1] == ['--internal-auto'] and len(args) == 2:
        request = helper.validate_request(json.loads(args[1]))
        if request['operation'] == 'auto':
            from agenticfan.hardware import native_hardware
            from agenticfan.runtime import lock, runtime_directory
            hw = native_hardware(include_temperatures=False)
            try:
                if any(i >= hw.count() for i in request['fans']):
                    raise invalid('Unknown fan.')
                with lock(runtime_directory(), 'write.lock'):
                    hw.restore(request['fans'], request['experimental_ftst'])
                emit(success({'fans': request['fans'], 'automatic_mode_verified': True}))
                return 0
            finally:
                hw.close()
    raise invalid('Unsupported service operation.')


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AgenticFanError as error:
        emit(failure(error))
        raise SystemExit(error.exit_code)
