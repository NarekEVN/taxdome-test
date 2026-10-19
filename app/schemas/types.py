import re
from typing import Annotated

from pydantic import AfterValidator, StrictStr, StringConstraints

from app.utils.money import parse_amount_to_cents

# C0 control characters and DEL, except tab, newline and carriage return. Postgres TEXT rejects NUL outright.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def normalize_uuid(value: str) -> str:
    if not _UUID_RE.fullmatch(value):
        raise ValueError("must be a UUID like '3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41'")
    return value.lower()


def _reject_control_chars(value: str) -> str:
    if _CONTROL_CHARS_RE.search(value):
        raise ValueError("must not contain control characters")
    return value


def _validate_amount(value: str) -> str:
    parse_amount_to_cents(value)
    return value


FirmUuid = Annotated[StrictStr, AfterValidator(normalize_uuid)]
Amount = Annotated[StrictStr, AfterValidator(_validate_amount)]
Description = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    AfterValidator(_reject_control_chars),
]
