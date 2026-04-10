# EPS UI Audit — Verification Checklist

Use this checklist to verify the fixes are working correctly after deployment.

## Pre-Test Setup

- [ ] Simulator is running and connected to MCS
- [ ] MCS server is running (port 9090)
- [ ] Browser can access http://localhost:9090/eps
- [ ] Browser console is open (F12) and cleared

## EPS Telemetry Display Tests

### Battery SOC (State of Charge)
- [ ] Displays initial value (not "---")
- [ ] Value is between 0-100
- [ ] Progress bar width matches percentage
- [ ] Progress bar color changes: green (>40%), yellow (20-40%), red (<20%)
- [ ] Value updates every ~1 second

### Bus Voltage
- [ ] Displays value in volts (e.g., "28.2 V")
- [ ] Value is between 20-30V nominal
- [ ] Text color changes: green (22-30V), yellow (20-22V or 30-32V), red (<20 or >32V)
- [ ] Updates every ~1 second

### Battery Voltage
- [ ] Displays value in volts
- [ ] Value is between 20-30V
- [ ] Updates every ~1 second

### Battery Current
- [ ] Displays value in amps (can be positive/negative)
- [ ] Updates every ~1 second

### Battery Temperature
- [ ] Displays value in °C
- [ ] Value is reasonable (typically -5 to +60°C)
- [ ] Updates every ~1 second

### Solar Array A Current
- [ ] Displays value in amps
- [ ] Value is positive (0-15A typical in sunlight)
- [ ] Drops to 0 during eclipse
- [ ] Updates every ~1 second

### Solar Array B Current
- [ ] Same behavior as SA-A
- [ ] Updates every ~1 second

### Power Generation
- [ ] Displays value in watts
- [ ] Value is positive in sunlight (typically 50-300W)
- [ ] Drops to near 0 during eclipse
- [ ] Green color applied
- [ ] Updates every ~1 second

### Power Consumption
- [ ] Displays value in watts
- [ ] Value is positive (typically 30-120W)
- [ ] Updates every ~1 second

## TCS Telemetry Display Tests (Requires TCS Model)

### OBC Temperature
- [ ] Displays value in °C (if TCS model running)
- [ ] Value is between -15 to 75°C
- [ ] Updates every ~60 seconds (HK interval for SID 3)
- [ ] **Or displays "---" if TCS model not running** ← Expected if tcs_basic.py not active

### Battery Temperature (TCS)
- [ ] Same behavior as OBC Temp
- [ ] Value is between -15 to 50°C
- [ ] Updates every ~60 seconds

### FPA Temperature
- [ ] Value is between -220 to -30°C (cryogenic)
- [ ] Updates every ~60 seconds

### Panel +X & +Y Temperatures
- [ ] Display values in °C
- [ ] Values vary -40 to +80°C based on illumination
- [ ] Updates every ~60 seconds

### Battery Heater Status
- [ ] Displays "ON" or "OFF" in badge
- [ ] Badge color: green if ON, gray if OFF
- [ ] Updates every ~60 seconds

### OBC Heater Status
- [ ] Same as Battery Heater
- [ ] Updates every ~60 seconds

### FPA Cooler Status
- [ ] Same badge behavior
- [ ] Updates every ~60 seconds

## Power History Chart Tests

- [ ] Canvas displays with grid lines
- [ ] Green line (Power Gen) updates smoothly
- [ ] Yellow/Orange line (Power Cons) updates smoothly
- [ ] Cyan line (SoC%) updates smoothly
- [ ] Legend shows three colors at bottom right
- [ ] Chart scrolls left as new data arrives (max 120 samples)
- [ ] Grid shows power scale (0W, 25W, 50W, 75W, 100W)

## Command Button Tests

### Solar Array A ON (func_id: 100)
- [ ] Button is visible and clickable
- [ ] Clicking sends TC message with service=8, subtype=1, function_id=100
- [ ] Event log shows "TC ACK S8/1 seq=X"
- [ ] Button does not raise JavaScript error

### Solar Array A OFF (func_id: 101)
- [ ] Same as SA-A ON
- [ ] Sends func_id=101

### Solar Array B ON (func_id: 102)
- [ ] Same as SA-A ON
- [ ] Sends func_id=102

### Solar Array B OFF (func_id: 103)
- [ ] Same as SA-A ON
- [ ] Sends func_id=103

### Battery Heater ON (func_id: 104)
- [ ] Same as SA-A ON
- [ ] Sends func_id=104

### Battery Heater OFF (func_id: 105)
- [ ] Same as SA-A ON
- [ ] Sends func_id=105

### OBC Heater ON (func_id: 106)
- [ ] Same as SA-A ON
- [ ] Sends func_id=106

### OBC Heater OFF (func_id: 107)
- [ ] Same as SA-A ON
- [ ] Sends func_id=107

## Event Log Tests

- [ ] Event log shows recent messages at top
- [ ] New messages appear immediately when TC ACK received
- [ ] Each message shows timestamp, category, and text
- [ ] Colors: green for OK, red for ERR, cyan for TM
- [ ] Log retains last 60 visible messages (up to 200 in buffer)

## TC Command Selector Tests

- [ ] Dropdown (#tc-sel) shows list of commands
- [ ] List includes commands with position='eps' from catalog
- [ ] Selecting a command populates #tc-fields with form inputs
- [ ] Form fields match command definition in tc_catalog.yaml
- [ ] SEND TC button sends command with correct structure

## Browser Console Tests

- [ ] No console.error() messages
- [ ] No uncaught exceptions
- [ ] No 404 errors loading resources
- [ ] WebSocket connects (check Network tab)
- [ ] State messages received regularly (Network tab)

## Network/Connectivity Tests

- [ ] WebSocket status shows "LINK OK" (green) in top right
- [ ] Reconnects automatically if connection drops
- [ ] After reconnect, telemetry resumes updating
- [ ] No data loss during reconnection

## Performance Tests

- [ ] Page loads in < 2 seconds
- [ ] Chart animation is smooth (no stuttering)
- [ ] No memory leaks (open DevTools, check memory over time)
- [ ] CPU usage is low (< 10% of one core)

## Overall Integration Tests

- [ ] All 9 EPS telemetry displays show values ✓
- [ ] Power chart updates smoothly ✓
- [ ] All 8 command buttons functional ✓
- [ ] Event log shows command ACKs ✓
- [ ] TCS displays either show values (if TCS model running) or "---" (if not)

---

## Known Limitations

1. **TCS Telemetry:** Displays show "---" if:
   - TCS model (tcs_basic.py) is not running in simulator
   - HK structure SID 3 is not subscribed
   - Server is not synthesizing TCS data in state message

2. **Command Handlers:** Commands are sent to simulator but execution depends on:
   - Service dispatch in simulator accepting func_id 100-107
   - Handler implemented for each legacy command
   - Power line model responding to state changes

3. **WebSocket Schema:** If server changes object structure of state message (e.g., property names), display will break silently with no errors

---

## Troubleshooting

### Issue: Telemetry shows "---"

**Causes:**
1. WebSocket not connected (check status indicator, top right)
2. Property name mismatch in onState() (check browser console for undefined warnings)
3. Parameter not in state message from server
4. Typo in HTML element ID

**Debug Steps:**
1. Check browser console: `console.log(window.state)` to see raw state object
2. Verify expected properties exist: `state.eps.soc_pct`, `state.tcs.temp_obc_C`
3. Check Network tab for state message payload
4. If property exists but display doesn't update, check setV() function in HTML

### Issue: Command button sends but no ACK

**Causes:**
1. func_id not in tc_catalog.yaml (verify func_id 100-107 exist)
2. Command handler in simulator not implemented for func_id
3. Service/subtype not routed to EPS dispatcher

**Debug Steps:**
1. Check server logs for TC message receipt
2. Verify tc_catalog.yaml has func_id definitions
3. Check simulator command handler for EPS service 8 dispatch
4. Inspect Network tab Raw data for actual message sent

### Issue: TCS displays all show "---"

**Causes:**
1. TCS model not running (most likely)
2. HK structure SID 3 not subscribed
3. Server not including tcs object in state message

**Debug Steps:**
1. Check simulator logs for tcs_basic.py initialization
2. Verify HK subscription for SID 3 on server startup
3. Check state message in Network tab for `tcs` property
4. If `tcs` property exists but empty, model is running but not computing

---

## Sign-Off

- [ ] All EPS telemetry displays verified working
- [ ] All 8 command buttons verified functional
- [ ] Power chart verified smooth and updating
- [ ] No console errors or warnings
- [ ] TCS telemetry status verified (green if running, amber if model not ready)
- [ ] Event log verified capturing commands

**Verified By:** ________________  **Date:** __________

**Notes:**
```
[Space for handwritten notes or additional findings]
```

