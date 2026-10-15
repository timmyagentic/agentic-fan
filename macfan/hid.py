"""Read-only Apple IOHID temperature events, including first-generation M1."""
from __future__ import annotations

import ctypes as C

from .errors import MacFanError
from .smc import bind
from .temperatures import reading


class HIDTemperatures:
    def __init__(self):
        self.client = None
        self.services = None
        self.entries = []
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.io = C.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        p = C.c_void_p
        self.release = bind(self.cf, "CFRelease", [p], None)
        self.create_string = bind(self.cf, "CFStringCreateWithCString", [p, C.c_char_p, C.c_uint32], p)
        self.string_bytes = bind(self.cf, "CFStringGetCString", [p, C.c_void_p, C.c_long, C.c_uint32], C.c_bool)
        self.type_id = bind(self.cf, "CFGetTypeID", [p], C.c_ulong)
        self.string_type = bind(self.cf, "CFStringGetTypeID", [], C.c_ulong)
        number = bind(self.cf, "CFNumberCreate", [p, C.c_int, p], p)
        dictionary = bind(self.cf, "CFDictionaryCreate", [p, p, p, C.c_long, p, p], p)
        count = bind(self.cf, "CFArrayGetCount", [p], C.c_long)
        item = bind(self.cf, "CFArrayGetValueAtIndex", [p, C.c_long], p)
        create_client = bind(self.io, "IOHIDEventSystemClientCreate", [p], p)
        matching = bind(self.io, "IOHIDEventSystemClientSetMatching", [p, p], None)
        services = bind(self.io, "IOHIDEventSystemClientCopyServices", [p], p)
        property_value = bind(self.io, "IOHIDServiceClientCopyProperty", [p, p], p)
        self.event = bind(self.io, "IOHIDServiceClientCopyEvent", [p, C.c_int64, C.c_int32, C.c_int64], p)
        self.event_value = bind(self.io, "IOHIDEventGetFloatValue", [p, C.c_uint32], C.c_double)
        registry_id = bind(self.io, "IOHIDServiceClientGetRegistryID", [p], C.c_uint64)

        objects = []
        try:
            keys = (p * 2)()
            values = (p * 2)()
            for i, (name, value) in enumerate((("PrimaryUsagePage", 0xFF00), ("PrimaryUsage", 5))):
                keys[i] = self.create_string(None, name.encode(), 0x08000100)
                raw_number = C.c_int32(value)
                values[i] = number(None, 3, C.byref(raw_number))
                objects.extend((keys[i], values[i]))
            key_callbacks = C.addressof(C.c_byte.in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks"))
            value_callbacks = C.addressof(C.c_byte.in_dll(self.cf, "kCFTypeDictionaryValueCallBacks"))
            match = dictionary(None, keys, values, 2, key_callbacks, value_callbacks)
            objects.append(match)
            self.client = create_client(None)
            if not self.client:
                raise MacFanError("HID_UNAVAILABLE", "Cannot create an IOHID temperature client.")
            matching(self.client, match)
            self.services = services(self.client)
            if not self.services:
                return
            product_key = self.create_string(None, b"Product", 0x08000100)
            objects.append(product_key)
            for i in range(count(self.services)):
                service = item(self.services, i)
                name = f"IOHID temperature {i}"
                product = property_value(service, product_key)
                if product:
                    try:
                        buffer = C.create_string_buffer(1024)
                        if self.type_id(product) == self.string_type() and self.string_bytes(product, buffer, len(buffer), 0x08000100):
                            name = buffer.value.decode("utf-8", errors="replace")
                    finally:
                        self.release(product)
                # Registry IDs are unique within a boot, not permanent hardware identifiers.
                self.entries.append((service, name, int(registry_id(service))))
        except BaseException:
            self.close()
            raise
        finally:
            for obj in reversed(objects):
                if obj:
                    self.release(obj)

    def read(self, timestamp: str) -> list[dict]:
        sensors = []
        for service, name, rid in self.entries:
            row = {"id": f"hid:{rid}", "name": name, "source": "iohid", "unit": "celsius",
                   **reading(unavailable_reason="no_event"), "sampled_at": timestamp,
                   "estimated": False, "identity_scope": "boot", "label_verified": False}
            event = self.event(service, 15, 0, 0)
            if event:
                try:
                    value = self.event_value(event, 15 << 16)
                    row.update(reading(value))
                finally:
                    self.release(event)
            else:
                row["error"] = {"code": "TEMPERATURE_EVENT_UNAVAILABLE", "message": "IOHID returned no temperature event.",
                                "details": {"sensor_id": row["id"]}}
            sensors.append(row)
        return sensors

    def close(self):
        if self.services:
            self.release(self.services)
            self.services = None
        if self.client:
            self.release(self.client)
            self.client = None
