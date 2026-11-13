"""Ids con prefijo + ULID (Crockford base32), como pide ARQ-01: fil_..., mod_..., etc."""

import os
import time

ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _base32(valor: int, largo: int) -> str:
    salida = []
    for _ in range(largo):
        salida.append(ALFABETO[valor % 32])
        valor //= 32
    return "".join(reversed(salida))


def ulid(ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if ms is None else ms
    return _base32(ms, 10) + _base32(int.from_bytes(os.urandom(10), "big"), 16)


def nuevo(prefijo: str) -> str:
    """nuevo("fil") -> "fil_01JC..." · eventId usa ulid() sin prefijo."""
    return f"{prefijo}_{ulid()}"
