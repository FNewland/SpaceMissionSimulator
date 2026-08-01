"""On-board memory map and sparse patchable memory image (PUS S6).

Before this module the S6 service was a façade: ``MEM_LOAD`` was logged and
thrown away, ``MEM_DUMP`` re-synthesised bytes from the address, ``MEM_CHECK``
CRC'd that same synthetic image (so a patch could never be verified), the
writable-region policy was a single hard-coded ``addr < 0x20000`` test, and the
map itself was hard-coded in three places that disagreed with each other
(defect #71).

What lives here now:

``MemoryRegion``
    One declared region: address, size, access (ro/rw), default fill, and an
    optional ``binding`` that ties the region to live model attributes.

``MemoryMap``
    The declared address space, loaded from
    ``configs/<mission>/memory/memory_map.yaml`` (``MemoryMap.load``) with a
    built-in default (``MemoryMap.default``) that reproduces the previously
    hard-coded EOSAT-1 layout, so minimal test config dirs keep working.

``MemoryImage``
    A **sparse page-backed store**. Pages are materialised only when written,
    so a patch to any RW region round-trips through ``MEM_DUMP`` and changes
    the ``MEM_CHECK`` CRC. Unwritten pages fall back to the region's declared
    fill pattern (the old synthetic content), so nothing that read memory
    before sees different bytes until somebody actually patches.

Bindings generalise the old one-off AOCS torque-gain special case: a bound
region reads its bytes *from* the live model and writes them *into* it, so a
patch changes spacecraft behaviour rather than just bytes in a buffer.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

PAGE_SIZE = 256

ACCESS_RO = "ro"
ACCESS_RW = "rw"

# Fill-pattern kinds a region may declare. `constant` uses `fill:`; the two
# `address_*` kinds reproduce the patterns the old _generate_memory_content()
# synthesised, so unpatched dumps are byte-identical to the previous behaviour.
FILL_ZERO = "zero"
FILL_CONSTANT = "constant"
FILL_ADDRESS_HIGH = "address_high"   # (addr >> 8) & 0xFF
FILL_ADDRESS_XOR = "address_xor"     # (addr & 0xFF) ^ ((addr >> 8) & 0xFF)

# S1 failure codes used by the S6 handlers.
ERR_ILLEGAL_ADDRESS = 0x0001   # address not inside any declared region
ERR_WRITE_PROTECTED = 0x0002   # region declares access: ro
ERR_BAD_ARGUMENT = 0x0003      # malformed request
ERR_LENGTH_EXCEEDED = 0x0004   # request longer than the configured limit


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


@dataclass
class BindingField:
    """One live value exposed inside a bound region."""
    offset: int
    attr: str
    type: str = "float32"
    key: str = ""       # argument passed to the model setter (e.g. axis "x")

    @property
    def size(self) -> int:
        return {"float32": 4, "uint32": 4, "uint16": 2, "uint8": 1}.get(self.type, 4)

    @property
    def struct_fmt(self) -> str:
        return {"float32": ">f", "uint32": ">I",
                "uint16": ">H", "uint8": ">B"}[self.type]


@dataclass
class RegionBinding:
    """Ties a region to attributes on a live subsystem model."""
    model: str
    fields: list[BindingField]
    setter: str = ""        # optional model method: setter(key, value)

    @property
    def size(self) -> int:
        return max((f.offset + f.size) for f in self.fields) if self.fields else 0


@dataclass
class MemoryRegion:
    name: str
    start: int
    size: int
    access: str = ACCESS_RW
    fill_kind: str = FILL_ZERO
    fill: int = 0x00
    description: str = ""
    patchable: bool = True
    binding: Optional[RegionBinding] = None

    @property
    def end(self) -> int:
        """Exclusive end address."""
        return self.start + self.size

    @property
    def writable(self) -> bool:
        return self.access == ACCESS_RW

    def contains(self, addr: int, length: int = 1) -> bool:
        return self.start <= addr and (addr + length) <= self.end

    def fill_byte(self, addr: int) -> int:
        if self.fill_kind == FILL_CONSTANT:
            return self.fill & 0xFF
        if self.fill_kind == FILL_ADDRESS_HIGH:
            return (addr >> 8) & 0xFF
        if self.fill_kind == FILL_ADDRESS_XOR:
            return (addr & 0xFF) ^ ((addr >> 8) & 0xFF)
        return 0x00

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "start": self.start,
            "size": self.size,
            "access": self.access,
            "description": self.description,
            "bound": self.binding is not None,
        }


@dataclass
class MemoryLimits:
    max_load_bytes: int = 512
    max_dump_bytes: int = 1024
    max_check_bytes: int = 0x100000


@dataclass
class MemoryMap:
    regions: list[MemoryRegion] = field(default_factory=list)
    limits: MemoryLimits = field(default_factory=MemoryLimits)

    # ── lookup ──────────────────────────────────────────────────────
    def region_at(self, addr: int, length: int = 1) -> Optional[MemoryRegion]:
        """Return the region wholly containing [addr, addr+length), else None."""
        for r in self.regions:
            if r.contains(addr, length):
                return r
        return None

    def is_mapped(self, addr: int, length: int = 1) -> bool:
        return self.region_at(addr, length) is not None

    def is_readonly(self, addr: int, length: int = 1) -> bool:
        r = self.region_at(addr, length)
        return r is not None and not r.writable

    def to_list(self) -> list[dict]:
        """Serialisable map, for `GET /api/memory-map` on the ground side."""
        return [r.to_dict() for r in self.regions]

    # ── construction ────────────────────────────────────────────────
    @classmethod
    def default(cls) -> "MemoryMap":
        """Built-in EOSAT-1 map, byte-compatible with the old hard-coded one."""
        return cls(regions=[
            MemoryRegion("boot_rom", 0x00000000, 0x10000, ACCESS_RO,
                         FILL_CONSTANT, 0x55, "Boot ROM (primary)", False),
            MemoryRegion("boot_backup", 0x00010000, 0x10000, ACCESS_RO,
                         FILL_CONSTANT, 0x55, "Boot ROM (backup copy)", False),
            MemoryRegion("app_a", 0x00100000, 0x80000, ACCESS_RW,
                         FILL_ADDRESS_HIGH, 0x00, "Application image A"),
            MemoryRegion("app_b", 0x00180000, 0x80000, ACCESS_RW,
                         FILL_ADDRESS_HIGH, 0x00, "Application image B"),
            MemoryRegion("config", 0x00200000, 0x20000, ACCESS_RW,
                         FILL_CONSTANT, 0xAA, "Configuration / parameter store"),
            MemoryRegion("scratch_ram", 0x20000000, 0x40000, ACCESS_RW,
                         FILL_ADDRESS_XOR, 0x00, "Scratchpad RAM"),
            MemoryRegion(
                "aocs_gains", 0x20100000, 12, ACCESS_RW, FILL_ZERO, 0x00,
                "AOCS per-axis control torque gains (live model binding)",
                True,
                RegionBinding(
                    model="aocs", setter="set_torque_gain",
                    fields=[
                        BindingField(0, "torque_gain_x", "float32", "x"),
                        BindingField(4, "torque_gain_y", "float32", "y"),
                        BindingField(8, "torque_gain_z", "float32", "z"),
                    ]),
            ),
        ])

    @classmethod
    def load(cls, config_dir: Path) -> "MemoryMap":
        """Load ``<config_dir>/memory/memory_map.yaml``; fall back to default."""
        path = Path(config_dir) / "memory" / "memory_map.yaml"
        if not path.exists():
            logger.info("No memory map at %s — using built-in default", path)
            return cls.default()
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except Exception as e:  # noqa: BLE001 — bad config must not block boot
            logger.warning("Memory map %s unreadable (%s) — using default", path, e)
            return cls.default()

        regions: list[MemoryRegion] = []
        for idx, item in enumerate(raw.get("regions") or []):
            try:
                binding = None
                b = item.get("binding")
                if isinstance(b, dict):
                    binding = RegionBinding(
                        model=str(b.get("model", "")),
                        setter=str(b.get("setter", "") or ""),
                        fields=[
                            BindingField(
                                offset=_as_int(f.get("offset"), 0),
                                attr=str(f.get("attr", "")),
                                type=str(f.get("type", "float32")),
                                key=str(f.get("key", "") or ""),
                            )
                            for f in (b.get("fields") or [])
                        ],
                    )
                regions.append(MemoryRegion(
                    name=str(item.get("name", f"region_{idx}")),
                    start=_as_int(item.get("start"), 0),
                    size=_as_int(item.get("size"), 0),
                    access=str(item.get("access", ACCESS_RW)).lower(),
                    fill_kind=str(item.get("fill_kind", FILL_ZERO)),
                    fill=_as_int(item.get("fill"), 0),
                    description=str(item.get("description", "")),
                    patchable=bool(item.get("patchable", True)),
                    binding=binding,
                ))
            except Exception as e:  # noqa: BLE001
                logger.warning("Memory map region %d invalid (%s) — skipped", idx, e)

        if not regions:
            logger.warning("Memory map %s declared no usable regions — using default",
                           path)
            return cls.default()

        # Overlap check: an ambiguous map would make access policy undefined.
        ordered = sorted(regions, key=lambda r: r.start)
        for a, b in zip(ordered, ordered[1:]):
            if b.start < a.end:
                logger.warning("Memory map: region %s overlaps %s", b.name, a.name)

        lim = raw.get("limits") or {}
        limits = MemoryLimits(
            max_load_bytes=_as_int(lim.get("max_load_bytes"), 512),
            max_dump_bytes=_as_int(lim.get("max_dump_bytes"), 1024),
            max_check_bytes=_as_int(lim.get("max_check_bytes"), 0x100000),
        )
        logger.info("Loaded memory map: %d regions from %s", len(regions), path)
        return cls(regions=ordered, limits=limits)


class MemoryImage:
    """Sparse, page-backed memory whose defaults come from the map.

    Only pages that have actually been written are materialised, so the image
    costs nothing until a patch lands. ``read`` composes written pages over the
    region fill pattern; bound regions are read from / written to the live
    model instead of the page store.
    """

    def __init__(self, memory_map: MemoryMap, engine=None):
        self.map = memory_map
        self._engine = engine
        self._pages: dict[int, bytearray] = {}

    # ── bindings ────────────────────────────────────────────────────
    def _model(self, name: str):
        subsystems = getattr(self._engine, "subsystems", None) or {}
        try:
            return subsystems.get(name)
        except Exception:  # noqa: BLE001 — MagicMock engines in unit tests
            return None

    def _binding_image(self, region: MemoryRegion) -> bytes | None:
        """Render a bound region's current model values as bytes."""
        b = region.binding
        model = self._model(b.model)
        state = getattr(model, "_state", None)
        if state is None:
            return None
        image = bytearray(region.size)
        for f in b.fields:
            value = getattr(state, f.attr, None)
            if value is None:
                return None
            try:
                packed = struct.pack(f.struct_fmt, float(value)
                                     if f.type == "float32" else int(value))
            except (struct.error, TypeError, ValueError):
                return None
            image[f.offset:f.offset + f.size] = packed
        return bytes(image)

    def _binding_write(self, region: MemoryRegion, addr: int, payload: bytes) -> None:
        """Push a patch of a bound region into the live model."""
        b = region.binding
        model = self._model(b.model)
        state = getattr(model, "_state", None)
        if state is None:
            return
        offset = addr - region.start
        for f in b.fields:
            lo = f.offset - offset
            if lo < 0 or lo + f.size > len(payload):
                continue  # this field not (fully) covered by the patch
            raw = payload[lo:lo + f.size]
            value = struct.unpack(f.struct_fmt, raw)[0]
            if b.setter and hasattr(model, b.setter):
                getattr(model, b.setter)(f.key or f.attr, value)
            else:
                setattr(state, f.attr, value)
            logger.info("S6 MEM_LOAD binding %s.%s = %s", b.model, f.attr, value)

    # ── access ──────────────────────────────────────────────────────
    def read(self, addr: int, length: int) -> bytes:
        """Return `length` bytes at `addr` (patched pages over region fill)."""
        if length <= 0:
            return b""
        out = bytearray()
        for i in range(length):
            cur = addr + i
            region = self.map.region_at(cur)
            if region is not None and region.binding is not None:
                image = self._binding_image(region)
                if image is not None:
                    idx = cur - region.start
                    out.append(image[idx] if idx < len(image) else 0x00)
                    continue
            page = self._pages.get(cur // PAGE_SIZE)
            if page is not None:
                out.append(page[cur % PAGE_SIZE])
            elif region is not None:
                out.append(region.fill_byte(cur))
            else:
                out.append(0x00)
        return bytes(out)

    def write(self, addr: int, payload: bytes) -> None:
        """Write `payload` at `addr`. Caller must have checked the access policy."""
        region = self.map.region_at(addr, len(payload))
        if region is not None and region.binding is not None:
            self._binding_write(region, addr, payload)
            return
        for i, byte in enumerate(payload):
            cur = addr + i
            page_no = cur // PAGE_SIZE
            page = self._pages.get(page_no)
            if page is None:
                base = page_no * PAGE_SIZE
                r = self.map.region_at(base) or self.map.region_at(cur)
                page = bytearray(
                    r.fill_byte(base + k) if r is not None else 0x00
                    for k in range(PAGE_SIZE)
                )
                self._pages[page_no] = page
            page[cur % PAGE_SIZE] = byte

    @property
    def patched_pages(self) -> int:
        return len(self._pages)

    def clear(self) -> None:
        self._pages.clear()

    # ── breakpoint persistence (mirrors OnboardTMStorage/ObcpManager) ──
    def get_state(self) -> dict:
        return {"pages": {str(n): bytes(p).hex() for n, p in self._pages.items()}}

    def set_state(self, state: dict) -> None:
        self._pages = {}
        for n, hexstr in (state or {}).get("pages", {}).items():
            try:
                self._pages[int(n)] = bytearray(bytes.fromhex(hexstr))
            except Exception:  # noqa: BLE001
                continue
