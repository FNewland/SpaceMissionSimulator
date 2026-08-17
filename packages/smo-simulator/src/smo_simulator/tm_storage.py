"""SMO Simulator — Onboard TM Packet Storage (S15).

Stores TM packets in onboard memory for later downlink.
Store 1 (HK) uses circular buffer (overwrites oldest when full).
Stores 2-4 (Event, Science, Alarm) use stop-when-full behaviour.
"""
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Default store definitions.
#
# HK_Store sizing: must hold ≥90 minutes of HK at the configured SID cadences
# so that a full orbit of data survives between ground passes.
#
# Per-second HK packet rate (from configs/eosat1/telemetry/hk_structures.yaml):
#   SID 1 (EPS)     @ 1 s  → 1.000
#   SID 2 (AOCS)    @ 4 s  → 0.250
#   SID 3 (TCS)     @ 60 s → 0.017
#   SID 4 (TTC)     @ 8 s  → 0.125
#   SID 5 (Payload) @ 8 s  → 0.125
#   SID 6 (OBDH)    @ 8 s  → 0.125
#   SID 11 (Beacon) @ 30 s → 0.033  (bootloader only, ignored in nominal)
#   ─────────────────────────────
#   Total nominal:           ~1.64 pkts/s
#
# 90 min × 60 s × 1.64 ≈ 8,870 packets. With 2× headroom for cadence changes,
# S3.27 on-demand reports, and occasional faster sampling during checkout we
# budget 18,000 slots. Circular so newest data always wins if something goes
# wrong and we miss a contact window.
DEFAULT_STORES = {
    1: {'name': 'HK_Store', 'capacity': 18000, 'circular': True},
    2: {'name': 'Event_Store', 'capacity': 2000, 'circular': False},
    3: {'name': 'Science_Store', 'capacity': 10000, 'circular': False},
    4: {'name': 'Alarm_Store', 'capacity': 500, 'circular': False},
}

# Service type to store ID routing.
#
# 2026-08b PL-c: this map only had S3 and S5 in it, and *everything else* fell
# through to store 3. The Science_Store therefore filled with S1 command
# acknowledgements and S8 function reports, and the scheduled
# S15_DUMP_SCIENCE_STORE — the one scheduled activity whose purpose is to
# return the mission's science — dumped acks. Imagery was not in it because no
# imagery existed anywhere (see service_dispatch `_handle_s13` and
# payload_basic `image_block`); now that products are real the store has to be
# routed for them.
#
# S1 verification and S8 function reports are spacecraft housekeeping, so they
# are named explicitly and land in the HK store. S13 blocks are deliberately
# NOT stored: the bulk pixels live in the payload's own mass memory
# (image_catalog / mem_used_mb) and S13 streams them to the ground on request —
# copying one 4 MB image into a 10 000-packet store as 4096 packets would evict
# everything else to hold a second copy of something already on board. What
# DOES reach store 3 is the 20-octet product record written by PAYLOAD_CAPTURE
# (service_dispatch `_image_record_tm`), which is what makes a science dump
# legible.
SERVICE_TO_STORE = {
    1: 1,   # Command verification → HK_Store  (was: fell through to Science)
    3: 1,   # HK → HK_Store
    5: 2,   # Events → Event_Store
    8: 1,   # Function-management reports → HK_Store  (was: ditto)
    12: 4,  # On-board monitoring limit checks → Alarm_Store  (was: ditto)
}

#: Services whose packets are never put in an onboard store (see above).
NOT_STORED = frozenset({13})

# Stores that use circular buffer behaviour
CIRCULAR_STORES = {1}


class OnboardTMStorage:
    """S15 Onboard TM packet storage with circular and stop-when-full modes."""

    def __init__(self, store_defs: dict | None = None,
                 time_source=None):
        """``time_source`` is a zero-argument callable returning **onboard**
        CUC seconds since the mission epoch (the OBDH clock — spec Part C.5),
        used to stamp any packet stored without an explicit timestamp. The
        engine wires it to ``engine._get_cuc_time``. Left None (bare unit-test
        storages) the stamps stay 0.0, as before.
        """
        defs = store_defs or DEFAULT_STORES
        self._time_source = time_source
        self._stores: dict[int, list] = {}
        self._capacities: dict[int, int] = {}
        self._enabled: dict[int, bool] = {}
        self._names: dict[int, str] = {}
        self._overflow: dict[int, bool] = {}
        self._circular: dict[int, bool] = {}
        self._oldest_ts: dict[int, float] = {}
        self._newest_ts: dict[int, float] = {}
        self._wrap_count: dict[int, int] = {}
        # Per-packet onboard timestamps, parallel to _stores[sid]. Kept so a
        # circular store's oldest_ts follows the eviction instead of reporting
        # the timestamp of a packet that was overwritten hours ago.
        self._ts: dict[int, list] = {}

        for store_id, info in defs.items():
            cap = info.get('capacity', 5000)
            self._stores[store_id] = []
            self._capacities[store_id] = cap
            self._enabled[store_id] = True
            self._names[store_id] = info.get('name', f'Store_{store_id}')
            self._overflow[store_id] = False
            self._circular[store_id] = info.get('circular', store_id in CIRCULAR_STORES)
            self._oldest_ts[store_id] = 0.0
            self._newest_ts[store_id] = 0.0
            self._wrap_count[store_id] = 0
            self._ts[store_id] = []

    def set_time_source(self, time_source) -> None:
        """Install the onboard clock used to stamp stored packets."""
        self._time_source = time_source

    def _onboard_now(self) -> float:
        """Onboard CUC seconds, or 0.0 when no clock is wired up."""
        if self._time_source is None:
            return 0.0
        try:
            return float(self._time_source())
        except Exception:  # noqa: BLE001 — storage must never break emission
            logger.warning("Onboard time source failed; storing unstamped")
            return 0.0

    def store_packet(self, service_type: int, pkt: bytes,
                     timestamp: float | None = None) -> bool:
        """Route a TM packet to the appropriate store by service type.

        ``timestamp`` is **onboard CUC seconds**; None reads the installed
        onboard clock. It used to default to 0.0, and every engine call site
        omitted it, so oldest_ts/newest_ts were never populated for the HK,
        event and science stores (spec Part C.5).

        Returns True if stored, False if store is full (linear) or disabled.
        """
        if service_type in NOT_STORED:
            return False
        store_id = SERVICE_TO_STORE.get(service_type)
        if store_id is None:
            # Science data (anything not HK or events) goes to science store
            store_id = 3

        return self.store_packet_direct(store_id, pkt, timestamp)

    def store_packet_direct(self, store_id: int, pkt: bytes,
                            timestamp: float | None = None) -> bool:
        """Store a packet directly into a specific store.

        ``timestamp`` is **onboard CUC seconds**; None reads the installed
        onboard clock, so the store timestamps match the CUC in the headers of
        the packets it holds.

        Returns True if stored. For circular stores, always succeeds (overwrites
        oldest). For linear stores, returns False if full.
        """
        if store_id not in self._stores:
            return False
        if not self._enabled.get(store_id, False):
            return False

        if timestamp is None:
            timestamp = self._onboard_now()
        timestamp = float(timestamp)

        store = self._stores[store_id]
        ts_list = self._ts.setdefault(store_id, [])
        cap = self._capacities[store_id]

        if len(store) >= cap:
            if self._circular.get(store_id, False):
                # Circular buffer: overwrite oldest packet
                store.pop(0)
                if ts_list:
                    ts_list.pop(0)
                self._overflow[store_id] = True
                self._wrap_count[store_id] += 1
            else:
                # Stop-when-full: reject packet, set overflow flag
                self._overflow[store_id] = True
                return False

        store.append(pkt)
        ts_list.append(timestamp)
        self._refresh_ts(store_id)
        return True

    def _refresh_ts(self, store_id: int) -> None:
        """Recompute oldest/newest onboard timestamps for a store.

        CUC 0 is a legitimate onboard time (the OBC boots at J2000 and every
        bootloader reset returns it there), so 0.0 is only treated as "no
        timestamp" when the whole store is unstamped.
        """
        stamped = [t for t in self._ts.get(store_id, ()) if t > 0]
        if stamped:
            self._oldest_ts[store_id] = stamped[0]
            self._newest_ts[store_id] = stamped[-1]

    def store_alarm(self, pkt: bytes, timestamp: float | None = None) -> bool:
        """Store a packet in the alarm buffer (store 4).

        ``timestamp`` is onboard CUC seconds — the alarm store used to be
        stamped with ``sim_time.timestamp()``, a Unix float, which could not
        be compared with the CUC in the headers of the packets inside it.
        """
        return self.store_packet_direct(4, pkt, timestamp)

    def start_dump(self, store_id: int) -> list[bytes]:
        """Return all stored packets for a store (S15.9)."""
        store = self._stores.get(store_id)
        if store is None:
            return []
        return list(store)

    def enable_store(self, store_id: int) -> None:
        if store_id in self._enabled:
            self._enabled[store_id] = True

    def disable_store(self, store_id: int) -> None:
        if store_id in self._enabled:
            self._enabled[store_id] = False

    def delete_store(self, store_id: int) -> None:
        """Clear all packets in a store and reset overflow."""
        if store_id in self._stores:
            self._stores[store_id].clear()
            self._ts.setdefault(store_id, []).clear()
            self._overflow[store_id] = False
            self._oldest_ts[store_id] = 0.0
            self._newest_ts[store_id] = 0.0

    def is_overflow(self, store_id: int) -> bool:
        """Check if a store has overflowed (hit capacity)."""
        return self._overflow.get(store_id, False)

    def get_state(self) -> dict:
        """Serialise the dynamic store state for a breakpoint snapshot.

        Packets are base64-encoded so the snapshot is JSON-safe. Static config
        (capacities, names, circular flags) is not serialised — it is rebuilt
        from the store definitions on construction and restored by key here.
        """
        return {
            "stores": {
                str(sid): [base64.b64encode(bytes(p)).decode("ascii") for p in pkts]
                for sid, pkts in self._stores.items()
            },
            "enabled": {str(k): v for k, v in self._enabled.items()},
            "overflow": {str(k): v for k, v in self._overflow.items()},
            "packet_ts": {str(k): list(v) for k, v in self._ts.items()},
            "oldest_ts": {str(k): v for k, v in self._oldest_ts.items()},
            "newest_ts": {str(k): v for k, v in self._newest_ts.items()},
            "wrap_count": {str(k): v for k, v in self._wrap_count.items()},
        }

    def set_state(self, state: dict) -> None:
        """Restore dynamic store state captured by get_state() (breakpoint load).

        Only stores that already exist (i.e. are defined) are restored; unknown
        store IDs in the snapshot are ignored.
        """
        if not state:
            return
        stores = state.get("stores", {})
        for sid_str, pkts in stores.items():
            sid = int(sid_str)
            if sid in self._stores:
                self._stores[sid] = [base64.b64decode(p) for p in pkts]
        for attr in ("enabled", "overflow", "oldest_ts", "newest_ts", "wrap_count"):
            target = getattr(self, f"_{attr}")
            for k_str, v in state.get(attr, {}).items():
                k = int(k_str)
                if k in target:
                    target[k] = v
        # Per-packet timestamps. Snapshots taken before they existed restore
        # as unstamped, and any length mismatch is padded/trimmed so the two
        # lists stay parallel.
        packet_ts = state.get("packet_ts", {})
        for sid in self._stores:
            n = len(self._stores[sid])
            vals = [float(t) for t in packet_ts.get(str(sid), [])][:n]
            vals = [0.0] * (n - len(vals)) + vals
            self._ts[sid] = vals

    def get_status(self) -> list[dict]:
        """Return status of all stores."""
        result = []
        for store_id in sorted(self._stores.keys()):
            cap = self._capacities[store_id]
            count = len(self._stores[store_id])
            result.append({
                'id': store_id,
                'name': self._names.get(store_id, ''),
                'count': count,
                'capacity': cap,
                'enabled': self._enabled.get(store_id, False),
                'fill_pct': round(count / cap * 100, 1) if cap > 0 else 0.0,
                'overflow': self._overflow.get(store_id, False),
                'circular': self._circular.get(store_id, False),
                'wrap_count': self._wrap_count.get(store_id, 0),
                'oldest_ts': self._oldest_ts.get(store_id, 0.0),
                'newest_ts': self._newest_ts.get(store_id, 0.0),
            })
        return result
