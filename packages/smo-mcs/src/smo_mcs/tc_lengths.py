"""Expected TC data-field lengths, derived from the TC catalogue.

The MCS command builder used to have no idea how many data bytes any command
needs: the S8.1 argument was a free-text hex box and the field definitions were
hand-maintained in ``index.html``.  Everything required to compute the answer is
already in ``configs/eosat1/commands/tc_catalog.yaml`` — this module does the
computation once, server-side, so ``/api/tc-catalog`` can hand the number to any
client (and so it is unit-testable).

The rule is::

    expected_data_bytes = sum(width(f.type) for f in cmd.fields)
                        + (1 if (service, subtype) == (8, 1) else 0)

The ``+1`` is the leading ``func_id`` byte, which is a sibling key of ``fields``
in the catalogue rather than a field, and which the simulator's dispatcher
strips before routing (``service_dispatch._route_*(func_id, data[1:])``).

Two families are *not* derivable and must never be shown as a number:

* four commands carry a ``bytes``-typed field of unbounded length
  (``MEM_LOAD``, ``SCHEDULE_TC``, ``OBCP_LOAD``, ``EA_ADD``);
* two commands have no ``fields`` key at all (``TTC_SWITCH_PRIMARY`` /
  ``TTC_SWITCH_REDUNDANT``).  "Absent" is unknown, not zero.

For those the caller gets ``expected_data_bytes = None`` plus a ``minimum`` and a
human-readable ``note`` so the UI can say "variable" with a hint.
"""
from __future__ import annotations

from typing import Any, Optional

__all__ = [
    "FIELD_WIDTHS",
    "VARIABLE_FIELD_TYPES",
    "field_width",
    "expected_data_bytes",
    "annotate_command",
]

#: Fixed on-the-wire width in bytes for every scalar field type used by the
#: catalogue.  Keep in sync with ``smo_mcs.server._PACK_FORMATS`` consumers and
#: with the simulator's ``struct`` formats.
FIELD_WIDTHS: dict[str, int] = {
    "uint8": 1,
    "int8": 1,
    "uint16": 2,
    "int16": 2,
    "uint32": 4,
    "int32": 4,
    "float32": 4,
    "float64": 8,
}

#: Field types whose length is not knowable from the catalogue.
VARIABLE_FIELD_TYPES = frozenset({"bytes", "string", "blob"})


def field_width(ftype: str) -> Optional[int]:
    """Width in bytes of a catalogue field type, or ``None`` if variable."""
    key = str(ftype or "").strip().lower()
    if key in VARIABLE_FIELD_TYPES:
        return None
    return FIELD_WIDTHS.get(key)


def expected_data_bytes(cmd: dict[str, Any]) -> dict[str, Any]:
    """Derive the expected data-field length of one catalogue command.

    Args:
        cmd: a command mapping with at least ``service``/``subtype`` and,
            normally, ``fields``.

    Returns:
        A dict with keys:

        ``expected_data_bytes``
            ``int`` when fully derivable, else ``None``.
        ``variable_length``
            ``True`` when the command carries an unbounded field.
        ``min_data_bytes``
            The fixed part — a lower bound that is always meaningful, and equal
            to ``expected_data_bytes`` when that is not ``None``.
        ``length_note``
            Short operator-facing explanation, or ``""`` when derivable.
    """
    service = int(cmd.get("service") or 0)
    subtype = int(cmd.get("subtype") or 0)
    # The S8.1 func_id byte precedes the declared fields.
    prefix = 1 if (service, subtype) == (8, 1) else 0

    if "fields" not in cmd or cmd.get("fields") is None:
        # Not "zero fields" — the catalogue simply does not say.
        return {
            "expected_data_bytes": None,
            "variable_length": False,
            "min_data_bytes": prefix,
            "length_note": (
                "catalogue entry has no 'fields' key — length unknown"
                + (f"; at least the {prefix} func_id byte" if prefix else "")
            ),
        }

    total = prefix
    variable = False
    unknown_types: list[str] = []
    for f in cmd.get("fields") or []:
        ftype = f.get("type") if isinstance(f, dict) else getattr(f, "type", None)
        width = field_width(ftype)
        if width is None:
            if str(ftype or "").strip().lower() in VARIABLE_FIELD_TYPES:
                variable = True
            else:
                unknown_types.append(str(ftype))
        else:
            total += width

    if unknown_types:
        return {
            "expected_data_bytes": None,
            "variable_length": False,
            "min_data_bytes": total,
            "length_note": "unknown field type(s): " + ", ".join(sorted(set(unknown_types))),
        }
    if variable:
        return {
            "expected_data_bytes": None,
            "variable_length": True,
            "min_data_bytes": total,
            "length_note": f"variable — {total} fixed byte(s) + payload",
        }
    return {
        "expected_data_bytes": total,
        "variable_length": False,
        "min_data_bytes": total,
        "length_note": "",
    }


def annotate_command(cmd: dict[str, Any]) -> dict[str, Any]:
    """Return ``cmd`` with the derived length keys merged in (in place)."""
    cmd.update(expected_data_bytes(cmd))
    return cmd
