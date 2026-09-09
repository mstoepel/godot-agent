"""Readers and writers for Godot's text file formats.

Three formats share one lexical core:

* ``project.godot``, ``*.import``, ``export_presets.cfg`` -- Godot's
  ``ConfigFile`` format (:mod:`godot_agent.gdformat.config`).
* ``*.tscn`` / ``*.tres`` -- the scene and resource format
  (:mod:`godot_agent.tscn`), which adds attributed section headers.
* Property values in both -- Godot variant literals
  (:mod:`godot_agent.gdformat.values`).
"""

from godot_agent.gdformat.config import GodotConfig, parse_config
from godot_agent.gdformat.values import (
    GDCall,
    GDNodePath,
    GDStringName,
    dumps,
    parse_value,
)

__all__ = [
    "GDCall",
    "GDNodePath",
    "GDStringName",
    "GodotConfig",
    "dumps",
    "parse_config",
    "parse_value",
]
