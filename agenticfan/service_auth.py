"""One local user's grant. External forms travel only on the root-authenticated socket."""
import base64
import ctypes as C
from contextlib import contextmanager

from .errors import AgenticFanError


class Item(C.Structure):
    _fields_ = [('name', C.c_char_p), ('length', C.c_size_t), ('value', C.c_void_p), ('flags', C.c_uint32)]


class Rights(C.Structure):
    _fields_ = [('count', C.c_uint32), ('items', C.POINTER(Item))]


Form = C.c_ubyte * 32


def security():
    lib = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
    ref, rights = C.c_void_p, C.POINTER(Rights)
    for name, args in {
        'AuthorizationCreate': [rights, rights, C.c_uint32, C.POINTER(ref)],
        'AuthorizationMakeExternalForm': [ref, C.POINTER(Form)],
        'AuthorizationCreateFromExternalForm': [C.POINTER(Form), C.POINTER(ref)],
        'AuthorizationCopyRights': [ref, rights, rights, C.c_uint32, C.POINTER(rights)],
        'AuthorizationFree': [ref, C.c_uint32],
    }.items():
        fn = getattr(lib, name); fn.argtypes = args; fn.restype = C.c_int32
    return lib


def check(status):
    if status:
        raise AgenticFanError('SERVICE_AUTHORIZATION_DENIED', 'Administrator authorization was not granted.', 4,
                              os_status=status)


def admin_right():
    return Rights(1, C.pointer(Item(b'system.privilege.admin', 0, None, 0)))


@contextmanager
def administrator_form():
    lib = security(); ref = C.c_void_p(); form = Form()
    check(lib.AuthorizationCreate(C.byref(admin_right()), None, 1 | 2 | 16, C.byref(ref)))
    try:
        check(lib.AuthorizationMakeExternalForm(ref, C.byref(form)))
        yield base64.b64encode(form).decode('ascii')
    finally:
        lib.AuthorizationFree(ref, 8)  # invalidate this one-use grant


def verify_form(encoded):
    try:
        form = Form.from_buffer_copy(base64.b64decode(encoded, validate=True))
    except (ValueError, TypeError):
        raise AgenticFanError('SERVICE_AUTHORIZATION_DENIED', 'Invalid authorization form.', 4) from None
    lib = security(); ref = C.c_void_p()
    check(lib.AuthorizationCreateFromExternalForm(C.byref(form), C.byref(ref)))
    try:
        # No interaction and no extending rights in the privileged process.
        check(lib.AuthorizationCopyRights(ref, C.byref(admin_right()), None, 0, None))
    finally:
        lib.AuthorizationFree(ref, 0)
