"""smo_common.commanding — the shared TC argument encoder/decoder (Phase E).

Single source of wire truth for telecommand arguments, driven entirely by
``configs/<mission>/commands/tc_catalog.yaml``. Every ground-side consumer
(procedure runner, OBCP loader, planner, S11 embedder, quick commands,
XTCE/SMDL generators, command reference) encodes through here; the MCS
browser builder mirrors this logic in JS and the cross-encoder agreement
test pins the two together byte-for-byte.
"""
from .catalog import (Catalog, FIELD_WIDTHS, clear_cache, default_catalog,
                      field_width, find_config_dir, load_catalog)
from .encode import (EncodeError, encode, encode_args, eng_to_raw,
                     field_default, raw_to_eng, resolve_command)
from .decode import DecodeError, decode, decode_args

__all__ = [
    "Catalog", "FIELD_WIDTHS", "clear_cache", "default_catalog",
    "field_width", "find_config_dir", "load_catalog",
    "EncodeError", "encode", "encode_args", "eng_to_raw", "field_default",
    "raw_to_eng", "resolve_command",
    "DecodeError", "decode", "decode_args",
]
