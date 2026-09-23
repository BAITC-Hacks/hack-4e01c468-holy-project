"""Stable fingerprints for Python code objects used in cache identities."""

from __future__ import annotations

import hashlib
import marshal
from types import CodeType
from typing import Any


def _code_identity(code: CodeType) -> tuple[Any, ...]:
    """Return stable code metadata without runtime-adapted bytecode state."""

    def stable_constant(value: Any) -> Any:
        if isinstance(value, CodeType):
            return _code_identity(value)
        if isinstance(value, tuple):
            return tuple(stable_constant(item) for item in value)
        if isinstance(value, frozenset):
            return tuple(sorted((stable_constant(item) for item in value), key=repr))
        return value

    return (
        code.co_argcount,
        code.co_posonlyargcount,
        code.co_kwonlyargcount,
        code.co_nlocals,
        code.co_stacksize,
        code.co_flags,
        code.co_code,
        tuple(stable_constant(value) for value in code.co_consts),
        code.co_names,
        code.co_varnames,
        code.co_freevars,
        code.co_cellvars,
        code.co_exceptiontable,
    )


def code_fingerprint(code: CodeType) -> str:
    """Hash code semantics without source locations or adaptive interpreter state."""
    return hashlib.sha256(marshal.dumps(_code_identity(code))).hexdigest()


_code_fingerprint = code_fingerprint
