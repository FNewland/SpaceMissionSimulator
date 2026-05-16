#!/usr/bin/env python3
"""generate_smdl.py — Generate SMP2 SMDL catalogues from YAML definitions.

Produces ECSS-E-ST-40-07C compliant Simulation Model Definition Language
(SMDL) catalogue files describing each subsystem model's fields, entry
points, and event sources for the EOSAT-1 mission.

Usage::

    python tools/generate_smdl.py
    python tools/generate_smdl.py --output-dir path/to/smdl/
"""

import argparse
import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path
from datetime import date

from xtce_smdl_common import (
    CONFIG, PACK_FORMAT_MAP, SID_SUBSYSTEM,
    load_parameters, load_hk_structures, load_commands,
    load_s12_definitions, load_mission_config,
    build_param_lookup, build_hk_param_formats,
    group_params_by_subsystem, group_commands_by_subsystem,
    smdl_uuid,
)

SMDL_XMLNS = "http://www.ecss.nl/smp/2019/Smdl"

# Entry points per subsystem (main step + subsystem-specific)
SUBSYSTEM_ENTRY_POINTS = {
    "EPS": ["Step", "UpdatePower", "UpdateBattery", "UpdateSolarArray"],
    "AOCS": ["Step", "UpdateAttitude", "UpdateSensors", "UpdateActuators"],
    "OBDH": ["Step", "UpdateScheduler", "UpdateMonitoring"],
    "TCS": ["Step", "UpdateThermal", "UpdateHeaters"],
    "TTC": ["Step", "UpdateLink", "UpdateTransponder"],
    "Payload": ["Step", "UpdateImager", "UpdateStorage"],
}

# SMP2 type names for pack formats
SMP2_TYPE_MAP = {
    "B": ("UInt8", "Integer", 1, False),
    "H": ("UInt16", "Integer", 2, False),
    "I": ("UInt32", "Integer", 4, False),
    "b": ("Int8", "Integer", 1, True),
    "h": ("Int16", "Integer", 2, True),
    "i": ("Int32", "Integer", 4, True),
    "f": ("Float32", "Float", 4, True),
}


def _el(parent, tag, attrib=None, text=None, **kwargs):
    a = dict(attrib or {})
    a.update(kwargs)
    e = ET.SubElement(parent, tag, a)
    if text is not None:
        e.text = str(text)
    return e


def _pretty_xml(root) -> str:
    rough = ET.tostring(root, encoding="unicode", xml_declaration=False)
    dom = minidom.parseString(rough)
    return dom.toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")


def _build_subsystem_catalogue(sub_name, params, commands, s12_rules,
                                param_formats, hk_structure):
    """Build a single subsystem SMDL catalogue."""
    root = ET.Element("Catalogue", {
        "xmlns": SMDL_XMLNS,
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "Name": sub_name,
        "Creator": "EOSAT-1 SMDL Generator",
        "Date": str(date.today()),
        "Version": "1.0",
    })
    _el(root, "Description", text=f"SMP2 model catalogue for {sub_name} subsystem")

    # ── Type definitions ──
    types_needed = set()
    for p in params:
        fmt = param_formats.get(p["id"], {})
        pack = fmt.get("pack_format", "f")
        types_needed.add(pack)

    for pack in sorted(types_needed):
        smp_name, smp_kind, size, signed = SMP2_TYPE_MAP.get(pack, ("Float32", "Float", 4, True))
        attribs = {
            "Name": smp_name,
            "Uuid": smdl_uuid(f"type_{sub_name}_{smp_name}"),
        }
        if smp_kind == "Integer":
            attribs["xsi:type"] = "Integer"
            attribs["Size"] = str(size)
            attribs["Signed"] = "true" if signed else "false"
        else:
            attribs["xsi:type"] = "Float"
            attribs["Size"] = str(size)
        _el(root, "Type", **attribs)

    # Bool type (for flags)
    _el(root, "Type", **{
        "xsi:type": "Bool",
        "Name": "Bool",
        "Uuid": smdl_uuid(f"type_{sub_name}_Bool"),
    })

    # ── Model definition ──
    model = _el(root, "Model",
                Name=f"{sub_name}_Model",
                Uuid=smdl_uuid(f"model_{sub_name}"))
    _el(model, "Description", text=f"{sub_name} subsystem simulation model")

    # Output fields (TM parameters)
    for p in params:
        fmt = param_formats.get(p["id"], {})
        pack = fmt.get("pack_format", "f")
        smp_name = SMP2_TYPE_MAP.get(pack, ("Float32",))[0]

        field = _el(model, "Field",
                     Name=p["xml_name"],
                     Uuid=smdl_uuid(f"field_{sub_name}_{p['xml_name']}"),
                     Type=smp_name,
                     Kind="Output",
                     View="true")
        desc = p.get("description", "")
        units = p.get("units", "")
        if desc or units:
            full_desc = desc
            if units:
                full_desc += f" [{units}]" if desc else f"[{units}]"
            _el(field, "Description", text=full_desc)

    # Input fields (from TC command arguments)
    for cmd in commands:
        for field_def in cmd.get("fields", []):
            fname = f"tc_{cmd['name']}_{field_def['name']}"
            # Map TC type to SMP2 type
            tc_type = field_def.get("type", "uint8")
            smp_type = {
                "uint8": "UInt8", "uint16": "UInt16", "uint32": "UInt32",
                "int16": "Int16", "int32": "Int32", "float32": "Float32",
            }.get(tc_type, "UInt8")

            f = _el(model, "Field",
                    Name=fname,
                    Uuid=smdl_uuid(f"field_{sub_name}_{fname}"),
                    Type=smp_type,
                    Kind="Input")
            if field_def.get("description"):
                _el(f, "Description", text=field_def["description"])

    # Entry points
    entry_points = SUBSYSTEM_ENTRY_POINTS.get(sub_name, ["Step"])
    for ep_name in entry_points:
        ep = _el(model, "EntryPoint",
                 Name=ep_name,
                 Uuid=smdl_uuid(f"ep_{sub_name}_{ep_name}"))
        _el(ep, "Description",
            text=f"{'Main simulation step' if ep_name == 'Step' else ep_name}")

    # Event sources (from S12 alarm rules)
    seen_events = set()
    for rule in s12_rules:
        pid = rule.get("param_id")
        severity = rule.get("severity", "WARNING")
        event_name = f"{rule.get('name', f'param_{pid}')}_{severity}"
        if event_name in seen_events:
            continue
        seen_events.add(event_name)
        ev = _el(model, "EventSource",
                 Name=event_name,
                 Uuid=smdl_uuid(f"event_{sub_name}_{event_name}"))
        _el(ev, "Description",
            text=f"{severity} alarm for {rule.get('name', f'param 0x{pid:04X}')}")

    return root


def generate_smdl(output_dir: Path):
    """Generate all SMDL catalogues."""
    params = load_parameters()
    hk_structures = load_hk_structures()
    commands = load_commands()
    s12_rules = load_s12_definitions()

    param_formats = build_hk_param_formats(hk_structures)
    param_groups = group_params_by_subsystem(params)
    cmd_groups = group_commands_by_subsystem(commands)

    hk_by_subsystem: dict[str, dict] = {}
    for s in hk_structures:
        sub = SID_SUBSYSTEM.get(s["sid"], "Spacecraft")
        hk_by_subsystem[sub] = s

    output_dir.mkdir(parents=True, exist_ok=True)

    # Per-subsystem catalogues
    subsystems = ["EPS", "AOCS", "OBDH", "TCS", "TTC", "Payload"]
    generated = []

    for sub_name in subsystems:
        sub_params = param_groups.get(sub_name, [])
        sub_cmds = cmd_groups.get(sub_name, [])
        hk_struct = hk_by_subsystem.get(sub_name)

        # Filter S12 rules for this subsystem
        sub_param_ids = {p["id"] for p in sub_params}
        sub_s12 = [r for r in s12_rules if r.get("param_id") in sub_param_ids]

        cat = _build_subsystem_catalogue(
            sub_name, sub_params, sub_cmds, sub_s12,
            param_formats, hk_struct)

        filename = f"{sub_name.lower()}.xml"
        outpath = output_dir / filename
        outpath.write_text(_pretty_xml(cat), encoding="utf-8")
        generated.append(filename)
        print(f"  {filename}: {len(sub_params)} params, {len(sub_cmds)} commands, "
              f"{len(sub_s12)} alarms")

    # Master catalogue
    master = ET.Element("Catalogue", {
        "xmlns": SMDL_XMLNS,
        "Name": "EOSAT-1",
        "Creator": "EOSAT-1 SMDL Generator",
        "Date": str(date.today()),
        "Version": "1.0",
    })
    _el(master, "Description",
        text="EOSAT-1 SMP2 Master Catalogue — references all subsystem models")

    for filename in generated:
        _el(master, "Implementation", Source=filename)

    master_path = output_dir / "eosat1_catalogue.xml"
    master_path.write_text(_pretty_xml(master), encoding="utf-8")

    print(f"\nSMDL generated in {output_dir}/")
    print(f"  Master: eosat1_catalogue.xml")
    print(f"  Subsystems: {', '.join(generated)}")
    print(f"  Total: {len(generated) + 1} files")


def main():
    parser = argparse.ArgumentParser(description="Generate SMP2 SMDL from YAML definitions")
    parser.add_argument("--output-dir", type=str,
                        default=str(CONFIG / "smdl"))
    args = parser.parse_args()
    generate_smdl(Path(args.output_dir))


if __name__ == "__main__":
    main()
