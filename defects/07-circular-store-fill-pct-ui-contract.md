## Summary

A fill percentage greater than 100 is physically impossible for a circular
store: `OnboardTMStorage.store_packet_direct` does `store.pop(0)` on
overflow, so `len(store)` is clamped at `capacity` and
`fill_pct = len(store) / capacity × 100` can never exceed 100. Any UI that
displays fill % > 100 for a circular store is therefore reading from the
wrong source or computing the value incorrectly. This is a UI contract
bug — it allows subtle data-source mismatches to pass silently.

## Severity

Minor — cosmetic, but enables larger bugs (see Defect #1) to be
misattributed to the S15 store when they actually come from elsewhere.

## Suggested fix

1. **Contract**: document that any widget labelled as a store fill
   percentage must read from `OnboardTMStorage.get_status()` and nowhere
   else. Add a comment to `tm_storage.py` and the MCS widget binding code.
2. **Runtime assertion**: add a `assert 0 <= fill_pct <= 100` in the
   widget binding layer (or a `clamp` call with a `logger.warning` if the
   value is out of range). A warning log is preferable to crashing a live
   display.
3. **Linter / test**: add a test that walks all MCS widgets tagged
   `"store_fill"` and asserts they bind to `tm_storage.get_status()`.
4. **Audit**: grep the MCS frontend for any widget labelled
   "Buffer Fill", "Store Fill", "%" + "buffer" to find every such
   widget and verify its data source.

## Acceptance criteria

- No MCS widget labelled as a store fill % reads from any source other
  than `OnboardTMStorage.get_status()`.
- Runtime assertion / clamp + warning in place.
- Test covers the widget-to-source contract.

## Related

- Defect #1: the OBDH "Buffer Fill – HK TM" widget showing 353% is the
  motivating case for this. That widget is clearly not bound to
  `tm_storage.get_status()`; it's reading an OBDH-internal counter
  instead. Fixing #1 also exercises this defect's contract.
