# Defect Register — SpaceMissionSoftware

Defects identified during the 2026-04-06 simulator review session. Each entry
below has a corresponding body file in `defects/` which is used verbatim by
`scripts/upload_and_file_defects.sh` when creating GitHub issues.

| #   | Severity | Status     | Title                                                                       |
| --- | -------- | ---------- | --------------------------------------------------------------------------- |
| 1   | Major    | Open       | OBDH "Buffer Fill – HK TM" parameter exceeds 100% (observed 353%)            |
| 2   | Major    | Fixed      | HK_Store sized for <1 orbit of housekeeping (5000 → 18000)                   |
| 3   | Major    | Fixed      | TM packets routed to S15 stores during bootloader (sw_image == 0)            |
| 4   | Major    | Open       | AOCS mode defaults to NOMINAL(4) at construction — "DETUMBLE at start"       |
| 5   | Critical | Open       | `sw_image` (0x0311) and `phase` (0x0129) not in any HK SID — unobservable    |
| 6   | Major    | Open       | MCS has no generic parameter-watch widget / no S20 client                    |
| 7   | Minor    | Open       | Fill % > 100 should be impossible for a circular store — UI contract bug    |
| 8   | Major    | Fixed      | Instructor display shows only ~30 params; no subsystem internals visible     |

Severity key:
- **Critical** — mission cannot operate safely without this
- **Major** — significant operational impact, workaround exists
- **Minor** — cosmetic or convenience, no operational impact

Fixed items are retained in the register so the associated regression tests
and rationale are captured in the issue tracker for audit.
