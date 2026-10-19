from __future__ import annotations


class AgenticFanError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 5, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class MissingKey(AgenticFanError):
    def __init__(self, key: str):
        super().__init__("KEY_NOT_FOUND", "The SMC key is absent.", key=key)


def invalid(message: str, **details) -> AgenticFanError:
    return AgenticFanError("INVALID_ARGUMENT", message, 2, **details)
