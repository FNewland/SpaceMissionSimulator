"""SMO Simulator — Onboard TM Packet Storage (S15).

Stores TM packets in onboard memory for later downlink.
Store 1 (HK) uses circular buffer (overwrites oldest when full).
Stores 2-4 (Event, Science, Alarm) use stop-when-full behaviour.
"""
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

# Service type to store ID routing
SERVICE_TO_STORE = {
    3: 1,   # HK → HK_Store
    5: 2,   # Events → Event_Store
}

# Stores that use circular buffer behaviour
CIRCULAR_STORES = {1}


class OnboardTMStorage:
    """S15 Onboard TM packet storage with circular and stop-when-full modes."""

    def __init__(self, store_defs: dict | None = None):
        defs = store_defs or DEFAULT_STORES
        self._stores: dict[int, list] = {}
        self._capacities: dict[int, int] = {}
        self._enabled: dict[int, bool] = {}
        self._names: dict[int, str] = {}
        self._overflow: dict[int, bool] = {}
        self._circular: dict[int, bool] = {}
        self._oldest_ts: dict[int, float] = {}
        self._newest_ts: dict[int, float] = {}
        self._wrap_count: dict[int, int] = {}

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

    def store_packet(self, service_type: int, pkt: bytes,
                     timestamp: float = 0.0) -> bool:
        """Route a TM packet to the appropriate store by service type.

        Returns True if stored, False if store is full (linear) or disabled.
        """
        store_id = SERVICE_TO_STORE.get(service_type)
        if store_id is None:
            # Science data (anything not HK or events) goes to science store
            store_id = 3

        return self.store_packet_direct(store_id, pkt, timestamp)

    def store_packet_direct(self, store_id: int, pkt: bytes,
                            timestamp: float = 0.0) -> bool:
        """Store a packet directly into a specific store.

        Returns True if stored. For circular stores, always succeeds (overwrites
        oldest). For linear stores, returns False if full.
        """
        if store_id not in self._stores:
            return False
        if not self._enabled.get(store_id, False):
            return False

        store = self._stores[store_id]
        cap = self._capacities[store_id]

        if len(store) >= cap:
            if self._circular.get(store_id, False):
                # Circular buffer: overwrite oldest packet
                store.pop(0)
                self._overflow[store_id] = True
                self._wrap_count[store_id] += 1
            else:
                # Stop-when-full: reject packet, set overflow flag
                self._overflow[store_id] = True
                return False

        store.append(pkt)

        # Track timestamps
        if timestamp > 0:
            if len(store) == 1:
                self._oldest_ts[store_id] = timestamp
            self._newest_ts[store_id] = timestamp

        return True

    def store_alarm(self, pkt: bytes, timestamp: float = 0.0) -> bool:
        """Store a packet in the alarm buffer (store 4)."""
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
            self._overflow[store_id] = False
            self._oldest_ts[store_id] = 0.0
            self._newest_ts[store_id] = 0.0

    def is_overflow(self, store_id: int) -> bool:
        """Check if a store has overflowed (hit capacity)."""
        return self._overflow.get(store_id, False)

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
