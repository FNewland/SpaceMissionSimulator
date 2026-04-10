"""SMO MCS — Display Rendering Engine.

Renders operator displays from YAML configuration using widgets.
"""
import json
import logging
from typing import Any

from smo_common.config.schemas import DisplayWidgetConfig, DisplayPageConfig, PositionConfig

logger = logging.getLogger(__name__)


class DisplayEngine:
    """Renders config-driven operator displays."""

    def __init__(self, positions: dict[str, PositionConfig]):
        self._positions = positions

    def get_position_names(self) -> list[str]:
        return list(self._positions.keys())

    def get_position_config(self, name: str) -> dict | None:
        pos = self._positions.get(name)
        if pos is None:
            return None
        return pos.model_dump() if hasattr(pos, 'model_dump') else {}

    def render_display_data(self, position: str, params: dict[int, float],
                            param_resolver: dict[str, int] | None = None) -> dict:
        """Render display data for a position given current parameters."""
        pos = self._positions.get(position)
        if pos is None:
            return {}
        result = {"label": pos.label, "pages": []}
        for page in pos.pages:
            page_data = {"name": page.name, "widgets": []}
            for widget in page.widgets:
                w_data = self._render_widget(widget, params, param_resolver)
                page_data["widgets"].append(w_data)
            result["pages"].append(page_data)
        return result

    def _render_widget(self, widget: DisplayWidgetConfig, params: dict,
                       resolver: dict | None = None) -> dict:
        w = {"type": widget.type, "label": widget.label}
        if widget.parameter:
            pid = resolver.get(widget.parameter) if resolver else None
            if pid is not None:
                w["value"] = params.get(pid, 0.0)
            w["parameter"] = widget.parameter
        if widget.parameters:
            w["values"] = {}
            for pname in widget.parameters:
                pid = resolver.get(pname) if resolver else None
                if pid is not None:
                    w["values"][pname] = params.get(pid, 0.0)
        if widget.range:
            w["range"] = widget.range
        if widget.units:
            w["units"] = widget.units
        return w
