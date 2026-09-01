"""Identity helpers for native and assembly-context Fusion entities.

Fusion queries may return a different Python proxy/wrapper for an entity that
was selected earlier.  Wrapper identity is therefore not physical BRep
identity.  The stable comparison for one open design is the native entity plus
the occurrence path that supplies its assembly context.
"""

from __future__ import annotations


def occurrence_path(entity) -> str | None:
    occurrence = getattr(entity, "assemblyContext", None)
    return getattr(occurrence, "fullPathName", None) if occurrence else None


def same_contextual_entity(left, right) -> bool:
    """Return whether two Fusion wrappers identify the same occurrence entity."""
    left_native = getattr(left, "nativeObject", None) or left
    right_native = getattr(right, "nativeObject", None) or right
    return (
        occurrence_path(left) == occurrence_path(right)
        and left_native == right_native
    )
