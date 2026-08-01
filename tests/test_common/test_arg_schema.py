"""Phase E WP-0 (brief 04) — the TC argument schema, validated mechanically.

Drift gate 2: for every command carrying the extended field schema, the
declared offsets, calibrations, ranges, defaults, enums and bit tables must
be internally consistent and must round-trip through the shared
encoder/decoder. Plus the two worked-example pins from the brief:

    encode("PAYLOAD_COOLER_SETPOINT", {"setpoint": -15.0}) == 24 64 D7
    encode("PAYLOAD_CAPTURE", {"scene_id": 1042, "lines": 2048}) == 1C 04 12 08 00
"""
from pathlib import Path

import pytest
import yaml

from smo_common.commanding import (EncodeError, decode, encode, encode_args,
                                   eng_to_raw, field_default, field_width,
                                   load_catalog, raw_to_eng)

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CONFIG_DIR)


@pytest.fixture(scope="module")
def commands(catalog):
    return catalog.commands


@pytest.fixture(scope="module")
def param_ids():
    params = yaml.safe_load(
        (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())
    plist = params.get("parameters") if isinstance(params, dict) else params
    return {int(p["id"]) for p in plist}


def _schema_commands(commands):
    return [c for c in commands if c.get("arg_schema_version")]


# ── worked examples (brief 04, exact bytes) ─────────────────────────

class TestWorkedExamples:
    def test_cooler_setpoint_minus_15(self, catalog):
        assert encode("PAYLOAD_COOLER_SETPOINT", {"setpoint": -15.0},
                      catalog=catalog) == bytes.fromhex("2464d7")

    def test_cooler_setpoint_table(self, catalog):
        # The brief's raw table: -20 -> 0x62E3, -10 -> 0x66CB, 0 -> 0x6AB3
        for eng, hexstr in [(-20.0, "2462e3"), (-10.0, "2466cb"),
                            (0.0, "246ab3")]:
            assert encode("PAYLOAD_COOLER_SETPOINT", {"setpoint": eng},
                          catalog=catalog) == bytes.fromhex(hexstr), eng

    def test_cooler_setpoint_minus_65_rejected_but_encodable(self, catalog):
        # -65 C is outside the Peltier envelope -> EncodeError with the range
        with pytest.raises(EncodeError):
            encode("PAYLOAD_COOLER_SETPOINT", {"setpoint": -65.0},
                   catalog=catalog)
        # ...but the ENCODING itself is trivial and positive (0x514F):
        raw = encode("PAYLOAD_COOLER_SETPOINT", {"setpoint": -65.0},
                     catalog=catalog, validate=False)
        assert raw == bytes.fromhex("24514f")

    def test_capture(self, catalog):
        assert encode("PAYLOAD_CAPTURE", {"scene_id": 1042, "lines": 2048},
                      catalog=catalog) == bytes.fromhex("1c04120800")

    def test_tx_power_levels(self, catalog):
        assert encode("TTC_SET_TX_POWER", {"level": 0},
                      catalog=catalog) == bytes.fromhex("4400")
        assert encode("TTC_SET_TX_POWER", {"level": 2},
                      catalog=catalog) == bytes.fromhex("4402")
        with pytest.raises(EncodeError):
            encode("TTC_SET_TX_POWER", {"level": 3}, catalog=catalog)

    def test_band_config_bitfield(self, catalog):
        assert encode("PAYLOAD_SET_BAND_CONFIG", {"mask": 0x07},
                      catalog=catalog) == bytes.fromhex("2107")
        with pytest.raises(EncodeError):   # reserved bits
            encode("PAYLOAD_SET_BAND_CONFIG", {"mask": 0x10}, catalog=catalog)

    def test_decode_round_trip(self, catalog):
        name, args = decode("24 64 D7", catalog=catalog)
        assert name == "PAYLOAD_COOLER_SETPOINT"
        assert args["setpoint"] == pytest.approx(-15.0, abs=1e-9)
        name, args = decode("1C 04 12 08 00", catalog=catalog)
        assert name == "PAYLOAD_CAPTURE"
        assert args == {"scene_id": 1042, "lines": 2048}


# ── mechanical schema validation over the whole catalogue ───────────

class TestSchemaConsistency:
    def test_offsets_contiguous(self, commands):
        """Declared offsets must equal the derived (order + width) offsets,
        starting at 1 for S8.1 (octet 0 is func_id) and 0 otherwise."""
        bad = []
        for cmd in commands:
            start = 1 if (cmd["service"], cmd["subtype"]) == (8, 1) else 0
            pos = start
            for f in cmd.get("fields") or []:
                w = field_width(f.get("type", "uint8"))
                declared = f.get("offset")
                if declared is not None and declared != pos:
                    bad.append(f"{cmd['name']}.{f['name']}: declared offset "
                               f"{declared}, derived {pos}")
                if w is None:
                    break          # variable tail
                pos += w
        assert not bad, "\n".join(bad)

    def test_ranges_round_trip_to_raw_range(self, commands):
        """`range` converted through `calibration` must land inside the
        field's integer width, and match `raw_range` where declared."""
        bad = []
        for cmd in _schema_commands(commands):
            for f in cmd.get("fields") or []:
                rng = f.get("range")
                ftype = str(f.get("type", "uint8")).lower()
                if rng is None or ftype.startswith("float"):
                    continue
                lo = eng_to_raw(f, rng["min"])
                hi = eng_to_raw(f, rng["max"])
                lo, hi = min(lo, hi), max(lo, hi)
                bits = (field_width(ftype) or 1) * 8
                if ftype.startswith("int"):
                    tmin, tmax = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
                else:
                    tmin, tmax = 0, (1 << bits) - 1
                if not (tmin <= lo and hi <= tmax):
                    bad.append(f"{cmd['name']}.{f['name']}: raw range "
                               f"[{lo},{hi}] does not fit {ftype}")
                rr = f.get("raw_range")
                if rr is not None and (lo != rr["min"] or hi != rr["max"]):
                    bad.append(f"{cmd['name']}.{f['name']}: declared "
                               f"raw_range [{rr['min']},{rr['max']}] != "
                               f"derived [{lo},{hi}]")
        assert not bad, "\n".join(bad)

    def test_defaults_inside_range(self, commands):
        bad = []
        for cmd in _schema_commands(commands):
            for f in cmd.get("fields") or []:
                d = f.get("default")
                rng = f.get("range")
                if d is None or rng is None:
                    continue
                if not (rng["min"] <= d <= rng["max"]):
                    bad.append(f"{cmd['name']}.{f['name']}: default {d} "
                               f"outside [{rng['min']},{rng['max']}]")
        assert not bad, "\n".join(bad)

    def test_enum_values_unique_and_fit_width(self, commands):
        bad = []
        for cmd in commands:
            for f in cmd.get("fields") or []:
                enum = f.get("enum") or []
                if not enum:
                    continue
                vals = [int(e["value"]) for e in enum]
                if len(set(vals)) != len(vals):
                    bad.append(f"{cmd['name']}.{f['name']}: duplicate enum "
                               f"values {vals}")
                bits = (field_width(f.get("type", "uint8")) or 1) * 8
                for v in vals:
                    if not (0 <= v < (1 << bits)):
                        bad.append(f"{cmd['name']}.{f['name']}: enum value "
                                   f"{v} does not fit {bits} bits")
                if f.get("default") is not None and \
                        int(f["default"]) not in vals:
                    bad.append(f"{cmd['name']}.{f['name']}: default "
                               f"{f['default']} not an enum value")
        assert not bad, "\n".join(bad)

    def test_bits_cover_width_without_duplicates(self, commands):
        bad = []
        for cmd in commands:
            for f in cmd.get("fields") or []:
                bits = f.get("bits") or []
                if not bits:
                    continue
                width_bits = (field_width(f.get("type", "uint8")) or 1) * 8
                seen = [int(b["bit"]) for b in bits]
                if len(set(seen)) != len(seen):
                    bad.append(f"{cmd['name']}.{f['name']}: duplicate bit "
                               f"indices {seen}")
                if sorted(seen) != list(range(width_bits)):
                    bad.append(f"{cmd['name']}.{f['name']}: bits {sorted(seen)} "
                               f"do not cover 0..{width_bits - 1}")
        assert not bad, "\n".join(bad)

    def test_readback_param_ids_resolve(self, commands, param_ids):
        bad = []
        for cmd in commands:
            rb = cmd.get("readback")
            if rb and int(rb["param_id"]) not in param_ids:
                bad.append(f"{cmd['name']}: readback param 0x"
                           f"{int(rb['param_id']):04X} not in parameters.yaml")
        assert not bad, "\n".join(bad)

    def test_encode_decode_round_trip_over_schema_commands(self, catalog,
                                                           commands):
        """Every fixed-width schema command round-trips defaults exactly."""
        for cmd in _schema_commands(commands):
            fields = cmd.get("fields") or []
            if any(str(f.get("type", "")).lower() in ("bytes", "string", "blob")
                   for f in fields):
                continue
            args = {f["name"]: field_default(f) for f in fields}
            try:
                wire = encode(cmd["name"], args, catalog=catalog)
            except EncodeError as exc:
                pytest.fail(f"{cmd['name']}: defaults do not encode: {exc}")
            if (cmd["service"], cmd["subtype"]) == (8, 1):
                name, back = decode(wire, catalog=catalog)
                assert name == cmd["name"]
            else:
                name, back = decode(wire, catalog=catalog, name=cmd["name"])
            for f in fields:
                fname = f["name"]
                if str(f.get("type", "")).lower().startswith("float"):
                    assert back[fname] == pytest.approx(float(args[fname]),
                                                        abs=1e-4), \
                        f"{cmd['name']}.{fname}"
                else:
                    scale = (f.get("calibration") or {}).get("scale", 1.0)
                    assert back[fname] == pytest.approx(args[fname],
                                                        abs=scale / 2 + 1e-9), \
                        f"{cmd['name']}.{fname}"

    def test_raw_to_eng_inverse(self, commands):
        for cmd in _schema_commands(commands):
            for f in cmd.get("fields") or []:
                if str(f.get("type", "")).lower().startswith("float"):
                    continue
                d = field_default(f)
                raw = eng_to_raw(f, d)
                back = raw_to_eng(f, raw)
                scale = (f.get("calibration") or {}).get("scale", 1.0)
                assert abs(float(back) - float(d)) <= abs(scale) / 2 + 1e-9, \
                    f"{cmd['name']}.{f['name']}: {d} -> {raw} -> {back}"


# ── quick_commands list (WP-5 drift gate) ───────────────────────────

class TestQuickCommandsEncode:
    def test_every_quick_command_encodes(self, catalog):
        assert catalog.quick_commands, "tc_catalog.yaml has no quick_commands"
        for qc in catalog.quick_commands:
            wire = encode(qc["command"], qc.get("args") or {}, catalog=catalog)
            cmd = catalog.get(qc["command"])
            # fixed-width commands: encoded length equals the declared length
            widths = [field_width(f.get("type", "uint8"))
                      for f in cmd.get("fields") or []]
            if all(w is not None for w in widths):
                expected = sum(widths) + (
                    1 if (cmd["service"], cmd["subtype"]) == (8, 1) else 0)
                assert len(wire) == expected, qc["command"]

    def test_payload_group_covers_the_ops_card(self, catalog):
        payload_qcs = [q for q in catalog.quick_commands
                       if q.get("group") == "PAYLOAD"]
        assert len(payload_qcs) >= 12, \
            f"payload quick-command group has only {len(payload_qcs)} buttons"
