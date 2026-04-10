"""SMO MCS — Display Widgets.

Reusable display widget definitions for MCS operator positions.
Enhanced with trending, limit overlays, and status color coding.
"""
from typing import Any, Optional
from collections import deque


class Widget:
    """Base class for display widgets."""
    widget_type: str = "base"

    def render(self, value: Any, config: dict) -> dict:
        return {"type": self.widget_type, "value": value}


class TrendingData:
    """Manages time-series data for trending."""

    def __init__(self, max_points: int = 300):
        self.max_points = max_points
        self.timestamps: deque = deque(maxlen=max_points)
        self.values: deque = deque(maxlen=max_points)

    def add_point(self, timestamp: float, value: float) -> None:
        """Add a data point."""
        self.timestamps.append(timestamp)
        self.values.append(value)

    def get_data(self) -> dict:
        """Get trending data for charting."""
        return {
            "timestamps": list(self.timestamps),
            "values": list(self.values),
            "count": len(self.values),
        }


class GaugeWidget(Widget):
    widget_type = "gauge"

    def render(self, value: float, config: dict) -> dict:
        # Determine status color based on limits
        status = self._get_status(value, config)
        return {
            "type": "gauge",
            "value": round(value, 2),
            "label": config.get("label", ""),
            "range": config.get("range", [0, 100]),
            "units": config.get("units", ""),
            "status": status,
            "limits": config.get("limits"),
        }

    @staticmethod
    def _get_status(value: float, config: dict) -> str:
        """Determine status based on limits."""
        limits = config.get("limits", {})
        if not limits:
            return "nominal"
        yellow_low = limits.get("yellow_low")
        yellow_high = limits.get("yellow_high")
        red_low = limits.get("red_low")
        red_high = limits.get("red_high")

        if red_low is not None and value <= red_low:
            return "alarm"
        if red_high is not None and value >= red_high:
            return "alarm"
        if yellow_low is not None and value <= yellow_low:
            return "warning"
        if yellow_high is not None and value >= yellow_high:
            return "warning"
        return "nominal"


class LineChartWidget(Widget):
    widget_type = "line_chart"

    def render(self, values: dict[str, list[float]], config: dict) -> dict:
        return {
            "type": "line_chart",
            "series": values,
            "label": config.get("label", ""),
            "duration_s": config.get("duration_s", 600),
            "show_limit_lines": config.get("show_limit_lines", True),
            "limits_ref": config.get("limits_ref"),
        }


class ValueTableWidget(Widget):
    widget_type = "value_table"

    def render(self, params: dict[str, float], config: dict) -> dict:
        return {
            "type": "value_table",
            "parameters": params,
        }


class StatusIndicatorWidget(Widget):
    widget_type = "status_indicator"

    def render(self, value: Any, config: dict) -> dict:
        return {
            "type": "status_indicator",
            "active": bool(value),
            "label": config.get("label", ""),
        }


class EventLogWidget(Widget):
    widget_type = "event_log"

    def render(self, events: list[dict], config: dict) -> dict:
        return {
            "type": "event_log",
            "events": events[-50:],  # last 50
        }
