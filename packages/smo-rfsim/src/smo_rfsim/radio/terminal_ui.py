"""Radio terminal UI using the rich library.

Displays a live-updating dashboard of RF link status indicators.
Falls back to plain-text output if rich is not installed.
"""

import asyncio
import logging
from typing import Optional

from .frontend import RadioFrontend, RadioStatus, LEDColor

logger = logging.getLogger(__name__)

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


def _led(color: LEDColor) -> str:
    if not HAS_RICH:
        return color.value
    colors = {LEDColor.GREEN: "[green]●[/]", LEDColor.YELLOW: "[yellow]●[/]",
              LEDColor.RED: "[red]●[/]"}
    return colors.get(color, "[white]○[/]")


def _format_status(status: RadioStatus) -> str:
    """Format status as a plain-text table."""
    lines = [
        "╔═══════════════════════════════════════════════╗",
        "║        RADIO FRONT-END STATUS                ║",
        "╠═══════════════════════════════════════════════╣",
        f"║ Mode:        {status.mode:<33}║",
        f"║ Carrier:     {status.carrier_lock.value:<20} {_led(status.carrier_led()):<12}║",
        f"║ Bit Sync:    {status.bit_sync.value:<20} {_led(status.bit_sync_led()):<12}║",
        f"║ Frame Sync:  {status.frame_sync.value:<20} {_led(status.frame_sync_led()):<12}║",
        "╠═══════════════════════════════════════════════╣",
        f"║ RSSI:        {status.rssi_dbm:>8.1f} dBm                    ║",
        f"║ Eb/N0:       {status.eb_n0_db:>8.1f} dB                     ║",
        f"║ BER:         10^{status.ber_log10:>5.1f}                       ║",
        f"║ Link Margin: {status.link_margin_db:>8.1f} dB                     ║",
        "╠═══════════════════════════════════════════════╣",
        f"║ Doppler:     {status.doppler_hz:>+10.1f} Hz                   ║",
        f"║ Range:       {status.range_km:>10.1f} km                   ║",
        f"║ Data Rate:   {status.data_rate_kbps:>8.1f} kbps                 ║",
        "╠═══════════════════════════════════════════════╣",
        f"║ Frames OK:   {status.good_frames:<10} Bad: {status.bad_frames:<14}║",
        f"║ CLTU Sent:   {status.cltu_sent:<10} Ack: {status.cltu_acked:<14}║",
        "╠═══════════════════════════════════════════════╣",
    ]
    for vcid in sorted(status.vc_active.keys()):
        led = _led(status.vc_led(vcid))
        lines.append(f"║ VC{vcid}:  {led}                                       ║")
    lines.append("╚═══════════════════════════════════════════════╝")
    return "\n".join(lines)


def _build_rich_table(status: RadioStatus) -> Table:
    """Build a rich Table for the Radio display."""
    table = Table(title="RADIO FRONT-END STATUS", border_style="bright_blue",
                  show_header=False, width=50)
    table.add_column("Parameter", style="cyan")
    table.add_column("Value", style="white")
    table.add_column("Status", style="white")

    table.add_row("Mode", status.mode, "")
    table.add_row("Carrier Lock", status.carrier_lock.value,
                  _led(status.carrier_led()))
    table.add_row("Bit Sync", status.bit_sync.value,
                  _led(status.bit_sync_led()))
    table.add_row("Frame Sync", status.frame_sync.value,
                  _led(status.frame_sync_led()))
    table.add_section()
    table.add_row("RSSI", f"{status.rssi_dbm:.1f} dBm", "")
    table.add_row("Eb/N0", f"{status.eb_n0_db:.1f} dB", "")
    table.add_row("BER", f"10^{status.ber_log10:.1f}", "")
    table.add_row("Link Margin", f"{status.link_margin_db:.1f} dB", "")
    table.add_section()
    table.add_row("Doppler", f"{status.doppler_hz:+.1f} Hz", "")
    table.add_row("Range", f"{status.range_km:.1f} km", "")
    table.add_row("Data Rate", f"{status.data_rate_kbps:.1f} kbps", "")
    table.add_section()
    table.add_row("Frames", f"OK: {status.good_frames}", f"Bad: {status.bad_frames}")
    table.add_row("CLTU", f"Sent: {status.cltu_sent}", f"Ack: {status.cltu_acked}")
    table.add_section()
    for vcid in sorted(status.vc_active.keys()):
        table.add_row(f"VC{vcid}", "", _led(status.vc_led(vcid)))
    return table


async def run_terminal_ui(frontend: RadioFrontend,
                          refresh_hz: float = 2.0):
    """Run the Radio terminal UI, refreshing at the given rate."""
    interval = 1.0 / refresh_hz

    if HAS_RICH:
        console = Console()
        with Live(console=console, refresh_per_second=refresh_hz) as live:
            while True:
                status = frontend.snapshot()
                live.update(_build_rich_table(status))
                await asyncio.sleep(interval)
    else:
        while True:
            status = frontend.snapshot()
            print("\033[2J\033[H" + _format_status(status))
            await asyncio.sleep(interval)
