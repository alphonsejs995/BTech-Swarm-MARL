# sensors/__init__.py
# Public API for the sensors package.
#
# MCP server usage:
#   from sensors.thermal import perform_scan, classify_heat, confirm_scan
#   from sensors.fog_of_war import FogOfWar

from .thermal import perform_scan, classify_heat, confirm_scan
from .fog_of_war import FogOfWar

__all__ = [
    "perform_scan",
    "classify_heat",
    "confirm_scan",
    "FogOfWar",
]
