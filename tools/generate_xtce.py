#!/usr/bin/env python3
"""generate_xtce.py — Generate XTCE XML from YAML TM/TC definitions.

Produces a CCSDS 660.1 compliant XTCE database file containing all
telemetry parameters, telecommands, HK packet structures, and monitoring
alarm definitions for the EOSAT-1 mission.

Usage::

    python tools/generate_xtce.py
    python tools/generate_xtce.py --output path/to/output.xml
"""

import argparse
import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path
from datetime import date

from xtce_smdl_common import (
    CONFIG, PACK_FORMAT_MAP, TC_TYPE_MAP, SID_SUBSYSTEM,
    load_parameters, load_hk_structures, load_commands,
    load_s12_definitions, load_mission_config,
    build_param_lookup, build_hk_param_formats,
    group_params_by_subsystem, group_commands_by_subsystem,
    xtce_uuid,
)

XTCE_NS = "http://www.omg.org/spec/XTCE/20180204"


def _el(parent, tag, attrib=None, text=None, **kwargs):
    """Create a child element with optional attributes and text."""
    a = dict(attrib or {})
    a.update(kwargs)
    # ElementTree requires all attribute values to be strings
    a = {k: str(v) for k, v in a.items()}
    e = ET.SubElement(parent, tag, a)
    if text is not None:
        e.text = str(text)
    return e


def _build_ccsds_system(root):
    """Build the CCSDS packet header SpaceSystem."""
    ss = _el(root, "SpaceSystem", name="CCSDS")
    _el(ss, "LongDescription", text="CCSDS Space Packet Protocol header definitions")

    tm = _el(ss, "TelemetryMetaData")
    pts = _el(tm, "ParameterTypeSet")
    # CCSDS header fields
    vt = _el(pts, "IntegerParameterType", name="VersionType", signed="false")
    _el(vt, "IntegerDataEncoding", sizeInBits="3", encoding="unsigned")
    for name, bits in [("PacketType", 1), ("SecHdrFlag", 1), ("APID", 11),
                       ("SeqFlags", 2), ("SeqCount", 14), ("DataLength", 16)]:
        pt = _el(pts, "IntegerParameterType", name=f"{name}Type", signed="false")
        _el(pt, "IntegerDataEncoding", sizeInBits=str(bits), encoding="unsigned")

    ps = _el(tm, "ParameterSet")
    for name in ["Version", "PacketType", "SecHdrFlag", "APID",
                  "SeqFlags", "SeqCount", "DataLength"]:
        _el(ps, "Parameter", name=f"ccsds_{name}", parameterTypeRef=f"{name}Type")

    cs = _el(tm, "ContainerSet")
    cc = _el(cs, "SequenceContainer", name="CCSDSPacket", abstract="true")
    el = _el(cc, "EntryList")
    for name in ["Version", "PacketType", "SecHdrFlag", "APID",
                  "SeqFlags", "SeqCount", "DataLength"]:
        _el(el, "ParameterRefEntry", parameterRef=f"ccsds_{name}")
    return ss


def _build_pus_system(root, mission_cfg):
    """Build the PUS secondary header SpaceSystem."""
    ss = _el(root, "SpaceSystem", name="PUS")
    tm = _el(ss, "TelemetryMetaData")
    pts = _el(tm, "ParameterTypeSet")
    for name, bits in [("PUSMisc", 8), ("Service", 8), ("Subtype", 8), ("CUCTime", 32)]:
        pt = _el(pts, "IntegerParameterType", name=f"PUS_{name}Type", signed="false")
        _el(pt, "IntegerDataEncoding", sizeInBits=str(bits), encoding="unsigned")

    # SID type (uint16, first field after PUS header in HK packets)
    pt = _el(pts, "IntegerParameterType", name="SIDType", signed="false")
    _el(pt, "IntegerDataEncoding", sizeInBits="16", encoding="unsigned")

    ps = _el(tm, "ParameterSet")
    for name in ["PUSMisc", "Service", "Subtype", "CUCTime"]:
        _el(ps, "Parameter", name=f"pus_{name}", parameterTypeRef=f"PUS_{name}Type")
    _el(ps, "Parameter", name="hk_SID", parameterTypeRef="SIDType")

    cs = _el(tm, "ContainerSet")
    # PUS TM packet (extends CCSDS)
    pus = _el(cs, "SequenceContainer", name="PUSPacket", abstract="true")
    bc = _el(pus, "BaseContainer", containerRef="/EOSAT-1/CCSDS/CCSDSPacket")
    rc = _el(bc, "RestrictionCriteria")
    cl = _el(rc, "ComparisonList")
    _el(cl, "Comparison", parameterRef="/EOSAT-1/CCSDS/ccsds_SecHdrFlag", value="1")
    el = _el(pus, "EntryList")
    for name in ["PUSMisc", "Service", "Subtype", "CUCTime"]:
        _el(el, "ParameterRefEntry", parameterRef=f"pus_{name}")

    # HK report packet (S3.25)
    hk = _el(cs, "SequenceContainer", name="HKPacket", abstract="true")
    bc2 = _el(hk, "BaseContainer", containerRef="PUSPacket")
    rc2 = _el(bc2, "RestrictionCriteria")
    cl2 = _el(rc2, "ComparisonList")
    _el(cl2, "Comparison", parameterRef="pus_Service", value="3")
    _el(cl2, "Comparison", parameterRef="pus_Subtype", value="25")
    el2 = _el(hk, "EntryList")
    _el(el2, "ParameterRefEntry", parameterRef="hk_SID")

    return ss


def _build_subsystem(root, subsystem_name, params, commands, hk_sid,
                     hk_structure, param_formats, s12_rules, param_lookup,
                     apid=1):
    """Build a subsystem SpaceSystem with TM parameters, HK container, and TC commands."""
    ss = _el(root, "SpaceSystem", name=subsystem_name)

    # ── Telemetry ──
    tm = _el(ss, "TelemetryMetaData")
    pts = _el(tm, "ParameterTypeSet")
    ps = _el(tm, "ParameterSet")

    # Build alarm lookup: param_id → list of (severity, low, high)
    alarm_map: dict[int, list] = {}
    for rule in s12_rules:
        pid = rule.get("param_id")
        if pid is not None:
            alarm_map.setdefault(pid, []).append(rule)

    for p in params:
        pid = p["id"]
        xml_name = p["xml_name"]
        units = p.get("units", "")
        desc = p.get("description", "")
        fmt = param_formats.get(pid, {})
        pack = fmt.get("pack_format", "f")
        scale = fmt.get("scale", 1)
        type_info = PACK_FORMAT_MAP.get(pack, PACK_FORMAT_MAP["f"])

        type_name = f"{xml_name}_Type"

        if type_info["xtce_type"] == "Float":
            pt = _el(pts, "FloatParameterType", name=type_name)
            enc = _el(pt, "FloatDataEncoding", sizeInBits=str(type_info["bits"]))
        else:
            signed = "true" if type_info["signed"] else "false"
            pt = _el(pts, "IntegerParameterType", name=type_name, signed=signed)
            enc = _el(pt, "IntegerDataEncoding",
                      sizeInBits=str(type_info["bits"]),
                      encoding="twosComplement" if type_info["signed"] else "unsigned")

        # Calibrator (if scale != 1)
        if scale != 1 and scale != 0:
            cal = _el(enc, "DefaultCalibrator")
            poly = _el(cal, "PolynomialCalibrator")
            _el(poly, "Term", coefficient=str(1.0 / scale), exponent="1")

        # Units
        if units:
            us = _el(pt, "UnitSet")
            _el(us, "Unit", text=units)

        # Alarm definitions
        if pid in alarm_map:
            alarm = _el(pt, "DefaultAlarm")
            ranges = _el(alarm, "StaticAlarmRanges")
            for rule in alarm_map[pid]:
                severity = rule.get("severity", "WARNING")
                low = rule.get("low_limit")
                high = rule.get("high_limit")
                tag = "WarningRange" if severity == "WARNING" else "CriticalRange"
                attribs = {}
                if low is not None:
                    attribs["minInclusive"] = str(low)
                if high is not None:
                    attribs["maxInclusive"] = str(high)
                if attribs:
                    _el(ranges, tag, **attribs)

        # Parameter instance
        param_el = _el(ps, "Parameter", name=xml_name, parameterTypeRef=type_name)
        if desc:
            _el(param_el, "LongDescription", text=desc)

    # ── HK SequenceContainer ──
    if hk_structure is not None:
        cs = _el(tm, "ContainerSet")
        sid = hk_structure["sid"]
        sid_name = hk_structure.get("name", f"SID{sid}")
        container_name = f"HK_{sid_name}_SID{sid}"

        sc = _el(cs, "SequenceContainer", name=container_name)
        bc = _el(sc, "BaseContainer",
                 containerRef=f"/EOSAT-1/PUS/HKPacket")
        rc = _el(bc, "RestrictionCriteria")
        cl = _el(rc, "ComparisonList")
        _el(cl, "Comparison", parameterRef="/EOSAT-1/PUS/hk_SID", value=str(sid))
        if apid != 1:
            _el(cl, "Comparison",
                parameterRef="/EOSAT-1/CCSDS/ccsds_APID", value=str(apid))

        el = _el(sc, "EntryList")
        for hk_param in hk_structure.get("parameters", []):
            pid = hk_param["param_id"]
            p_def = param_lookup.get(pid)
            if p_def:
                _el(el, "ParameterRefEntry",
                    parameterRef=f"/EOSAT-1/{subsystem_name}/{p_def['xml_name']}")

    # ── Commands ──
    if commands:
        cmd_meta = _el(ss, "CommandMetaData")
        ats = _el(cmd_meta, "ArgumentTypeSet")
        mcs = _el(cmd_meta, "MetaCommandSet")

        # Argument types used by commands in this subsystem
        arg_types_needed = set()
        for cmd in commands:
            for field in cmd.get("fields", []):
                ft = field.get("type", "uint8")
                arg_types_needed.add(ft)

        for ft in sorted(arg_types_needed):
            ti = TC_TYPE_MAP.get(ft)
            if not ti:
                continue
            if ti.get("binary"):
                _el(ats, "BinaryArgumentType", name=f"{ft}_ArgType")
            elif ti.get("float"):
                at = _el(ats, "FloatArgumentType", name=f"{ft}_ArgType",
                         sizeInBits=str(ti["bits"]))
                _el(at, "FloatDataEncoding", sizeInBits=str(ti["bits"]))
            else:
                signed = "true" if ti["signed"] else "false"
                at = _el(ats, "IntegerArgumentType", name=f"{ft}_ArgType",
                         signed=signed)
                _el(at, "IntegerDataEncoding", sizeInBits=str(ti["bits"]),
                    encoding="twosComplement" if ti["signed"] else "unsigned")

        for cmd in commands:
            mc = _el(mcs, "MetaCommand", name=cmd["name"])
            if cmd.get("description"):
                _el(mc, "LongDescription", text=cmd["description"])

            # Significance for critical commands
            crit = cmd.get("criticality")
            if crit:
                level = "critical" if crit == "critical" else "caution"
                _el(mc, "DefaultSignificance", consequenceLevel=level)

            args = cmd.get("fields", [])
            if args:
                al = _el(mc, "ArgumentList")
                for field in args:
                    ft = field.get("type", "uint8")
                    arg = _el(al, "Argument", name=field["name"],
                              argumentTypeRef=f"{ft}_ArgType")
                    if field.get("description"):
                        _el(arg, "LongDescription", text=field["description"])

    return ss


def generate_xtce(output_path: Path):
    """Generate the complete XTCE database."""
    params = load_parameters()
    hk_structures = load_hk_structures()
    commands = load_commands()
    s12_rules = load_s12_definitions()
    mission_cfg = load_mission_config()

    param_lookup = build_param_lookup(params)
    param_formats = build_hk_param_formats(hk_structures)
    param_groups = group_params_by_subsystem(params)
    cmd_groups = group_commands_by_subsystem(commands)

    # HK structure lookup by SID → subsystem
    hk_by_subsystem: dict[str, dict] = {}
    for s in hk_structures:
        sub = SID_SUBSYSTEM.get(s["sid"], "Spacecraft")
        hk_by_subsystem[sub] = s

    # Build root SpaceSystem
    root = ET.Element("SpaceSystem", {
        "name": "EOSAT-1",
        "xmlns": XTCE_NS,
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
    })

    header = _el(root, "Header",
                 date=str(date.today()),
                 version="1.0",
                 classification="Unclassified — Simulation")

    # CCSDS and PUS base systems
    _build_ccsds_system(root)
    _build_pus_system(root, mission_cfg)

    # Per-subsystem SpaceSystems
    subsystem_order = ["EPS", "AOCS", "TCS", "OBDH", "TTC", "Payload",
                       "Beacon", "FDIR", "Spacecraft"]
    for sub_name in subsystem_order:
        sub_params = param_groups.get(sub_name, [])
        sub_cmds = cmd_groups.get(sub_name, [])
        hk_struct = hk_by_subsystem.get(sub_name)

        # Filter S12 rules for this subsystem's parameters
        sub_param_ids = {p["id"] for p in sub_params}
        sub_s12 = [r for r in s12_rules if r.get("param_id") in sub_param_ids]

        apid = 2 if sub_name == "Beacon" else 1

        if sub_params or sub_cmds:
            _build_subsystem(root, sub_name, sub_params, sub_cmds,
                             hk_struct.get("sid") if hk_struct else None,
                             hk_struct, param_formats, sub_s12,
                             param_lookup, apid=apid)

    # Pretty-print
    rough = ET.tostring(root, encoding="unicode", xml_declaration=False)
    dom = minidom.parseString(rough)
    pretty = dom.toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(pretty, encoding="utf-8")
    print(f"XTCE generated: {output_path}")
    print(f"  Parameters: {len(params)}")
    print(f"  Commands: {len(commands)}")
    print(f"  HK structures: {len(hk_structures)}")
    print(f"  Monitoring rules: {len(s12_rules)}")


def main():
    parser = argparse.ArgumentParser(description="Generate XTCE from YAML TM/TC definitions")
    parser.add_argument("--output", type=str,
                        default=str(CONFIG / "xtce" / "eosat1.xml"))
    args = parser.parse_args()
    generate_xtce(Path(args.output))


if __name__ == "__main__":
    main()
