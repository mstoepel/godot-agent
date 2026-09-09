"""A node-tree view over a parsed scene file.

:mod:`godot_agent.tscn.parser` gives a flat list of sections, which is what the
file literally is. This module layers the tree back on top: resolving node
paths, inserting a node in a position the engine will accept, allocating
resource ids, and rendering a compact tree the model can read cheaply.

Ordering is the subtle part. Godot requires a node's parent to appear earlier
in the file, and it reads ``parent`` as a path relative to the scene root. A
new child is therefore inserted after the last existing descendant of its
parent, not merely after the parent itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from godot_agent.gdformat.values import dumps
from godot_agent.tscn.parser import Section, TscnFile, TscnParseError, read_tscn

__all__ = ["Scene", "SceneNode", "format_member"]

#: Path Godot uses for the scene root in a ``parent`` attribute.
ROOT_PATH = "."


@dataclass(frozen=True, slots=True)
class SceneNode:
    """One node in the scene, paired with its backing section."""

    path: str
    name: str
    type: str | None
    parent: str | None
    section: Section

    @property
    def is_root(self) -> bool:
        return self.parent is None

    @property
    def is_instance(self) -> bool:
        """True when the node instantiates a packed scene instead of a type."""
        return "instance" in self.section.attributes


def format_member(node_path: str | None, member: str | None) -> str:
    """Render ``node.member`` for display, without doubling the root's dot.

    The scene root's path is literally ``"."``, so naive joining produces
    ``".._on_body_entered"``, which reads like a broken path.
    """
    node = node_path or ROOT_PATH
    label = "<root>" if node == ROOT_PATH else node
    return f"{label}.{member}"


def _child_path(parent: str | None, name: str) -> str:
    if parent is None:
        return ROOT_PATH
    if parent == ROOT_PATH:
        return name
    return f"{parent}/{name}"


class Scene:
    """A mutable node-tree view over a :class:`~godot_agent.tscn.parser.TscnFile`."""

    def __init__(self, file: TscnFile, source_path: Path | None = None) -> None:
        self.file = file
        self.source_path = source_path

    # -- construction -----------------------------------------------------

    @classmethod
    def read(cls, path: Path | str) -> Scene:
        """Read a scene from disk."""
        resolved = Path(path)
        return cls(read_tscn(resolved), resolved)

    def write(self, path: Path | str | None = None) -> Path:
        """Write the scene back to disk and return the path written."""
        target = Path(path) if path is not None else self.source_path
        if target is None:
            raise ValueError("no path given and this scene was not read from disk")
        self.file.write(target)
        return target

    # -- reading ----------------------------------------------------------

    @property
    def nodes(self) -> list[SceneNode]:
        """Every node, in file order, with resolved paths."""
        out: list[SceneNode] = []
        for section in self.file.nodes:
            name = section.attr_str("name")
            if name is None:
                raise TscnParseError(f"node section without a name: {section.header_text()}")
            parent = section.attr_str("parent")
            out.append(
                SceneNode(
                    path=_child_path(parent, name),
                    name=name,
                    type=section.attr_str("type"),
                    parent=parent,
                    section=section,
                )
            )
        return out

    @property
    def root(self) -> SceneNode | None:
        """The scene root, or ``None`` for an empty scene."""
        for node in self.nodes:
            if node.is_root:
                return node
        return None

    def find(self, path: str) -> SceneNode | None:
        """Return the node at ``path`` (``.`` for the root), or ``None``."""
        normalized = path.strip() or ROOT_PATH
        for node in self.nodes:
            if node.path == normalized:
                return node
        return None

    def children_of(self, path: str) -> list[SceneNode]:
        """Return the direct children of the node at ``path``."""
        return [node for node in self.nodes if node.parent == path]

    def describe(self, max_properties: int = 6) -> str:
        """Render the tree as indented text for the model to read.

        Keeps a bounded number of properties per node so a large scene stays
        affordable to look at; the model can read the file for the rest.
        """
        lines: list[str] = []
        by_depth = {ROOT_PATH: 0}
        for node in self.nodes:
            depth = 0 if node.is_root else by_depth.get(node.parent or ROOT_PATH, 0) + 1
            by_depth[node.path] = depth
            kind = node.type or (
                f"instance {node.section.attr_str('instance', '?')}" if node.is_instance else "?"
            )
            lines.append(f"{'  ' * depth}{node.name} [{kind}]")
            for index, (key, value) in enumerate(node.section.properties.items()):
                if index >= max_properties:
                    remaining = len(node.section.properties) - max_properties
                    lines.append(f"{'  ' * (depth + 1)}... {remaining} more properties")
                    break
                flat = " ".join(value.split())
                if len(flat) > 80:
                    flat = flat[:77] + "..."
                lines.append(f"{'  ' * (depth + 1)}{key} = {flat}")
        for connection in self.file.connections:
            lines.append(
                f"signal {connection.attr_str('signal')}: "
                f"{connection.attr_str('from')} -> "
                f"{format_member(connection.attr_str('to'), connection.attr_str('method'))}"
            )
        return "\n".join(lines)

    # -- mutation ---------------------------------------------------------

    def add_node(
        self,
        name: str,
        type_: str,
        parent: str = ROOT_PATH,
        properties: dict[str, object] | None = None,
    ) -> SceneNode:
        """Add a node under ``parent`` and return it.

        Raises:
            ValueError: if ``parent`` does not exist, or a sibling already uses
                ``name`` -- Godot silently renames duplicates, which would
                break every ``NodePath`` the agent just wrote.
        """
        if not self.file.nodes:
            raise ValueError("cannot add a child to an empty scene; add a root node first")
        if self.find(parent) is None:
            raise ValueError(f"parent node {parent!r} does not exist in this scene")
        target_path = _child_path(parent, name)
        if self.find(target_path) is not None:
            raise ValueError(f"a node named {name!r} already exists under {parent!r}")

        section = Section(kind="node")
        section.set_attr("name", name)
        section.set_attr("type", type_)
        section.set_attr("parent", parent)
        for key, value in (properties or {}).items():
            section.set(key, value)

        self.file.sections.insert(self._insertion_index(parent), section)
        node = self.find(target_path)
        assert node is not None  # just inserted
        return node

    def _insertion_index(self, parent: str) -> int:
        """Index just past the last node that is ``parent`` or beneath it.

        Inserting merely *after the parent* is not enough: the parent's own
        existing children sit between it and the next unrelated node, and
        splitting that run would leave a node ahead of its parent.

        Appending under the root lands after the last node but still before the
        ``[connection]`` block, which the engine expects at the end.
        """
        prefix = "" if parent == ROOT_PATH else f"{parent}/"
        last: int | None = None
        for index, section in enumerate(self.file.sections):
            if section.kind != "node":
                continue
            path = _child_path(section.attr_str("parent"), section.attr_str("name") or "")
            if parent == ROOT_PATH or path == parent or path.startswith(prefix):
                last = index
        return len(self.file.sections) if last is None else last + 1

    def remove_node(self, path: str) -> None:
        """Remove the node at ``path`` and everything beneath it."""
        node = self.find(path)
        if node is None:
            raise ValueError(f"node {path!r} does not exist in this scene")
        if node.is_root:
            raise ValueError("refusing to remove the scene root")
        prefix = f"{path}/"
        doomed = {
            candidate.section
            for candidate in self.nodes
            if candidate.path == path or candidate.path.startswith(prefix)
        }
        self.file.sections = [s for s in self.file.sections if s not in doomed]

    def set_property(self, path: str, key: str, value: object) -> None:
        """Set one property on the node at ``path``."""
        node = self.find(path)
        if node is None:
            raise ValueError(f"node {path!r} does not exist in this scene")
        node.section.set(key, value)

    def add_ext_resource(self, type_: str, res_path: str, uid: str | None = None) -> str:
        """Add an external resource reference and return its id.

        Reuses an existing entry for the same path, so repeated calls from
        different tools do not accumulate duplicates.
        """
        for section in self.file.ext_resources:
            if section.attr_str("path") == res_path:
                return section.attr_str("id") or ""

        index = len(self.file.ext_resources) + 1
        resource_id = f"{index}_{_slug(res_path)}"
        section = Section(kind="ext_resource")
        section.set_attr("type", type_)
        if uid:
            section.set_attr("uid", uid)
        section.set_attr("path", res_path)
        section.set_attr("id", resource_id)

        insert_at = 1 if self.file.descriptor else 0
        for position, existing in enumerate(self.file.sections):
            if existing.kind == "ext_resource":
                insert_at = position + 1
        self.file.sections.insert(insert_at, section)
        self._refresh_load_steps()
        return resource_id

    def add_connection(self, signal: str, from_path: str, to_path: str, method: str) -> None:
        """Connect ``signal`` on ``from_path`` to ``method`` on ``to_path``."""
        for existing in self.file.connections:
            if (
                existing.attr_str("signal") == signal
                and existing.attr_str("from") == from_path
                and existing.attr_str("to") == to_path
                and existing.attr_str("method") == method
            ):
                return
        section = Section(kind="connection")
        section.set_attr("signal", signal)
        section.set_attr("from", from_path)
        section.set_attr("to", to_path)
        section.set_attr("method", method)
        self.file.sections.append(section)

    def _refresh_load_steps(self) -> None:
        """Keep ``load_steps`` in step with the resource count.

        Godot uses it only as a progress-bar hint and recomputes it on save, but
        an obviously wrong value is a red flag to anyone reading the diff.
        """
        descriptor = self.file.descriptor
        if descriptor is None or "load_steps" not in descriptor.attributes:
            return
        count = len(self.file.ext_resources) + len(self.file.sub_resources) + 1
        descriptor.attributes["load_steps"] = dumps(count)


def _slug(res_path: str) -> str:
    """Build the short suffix Godot appends to a resource id."""
    stem = Path(res_path).stem
    cleaned = "".join(char if char.isalnum() else "_" for char in stem)
    return cleaned[:16] or "res"
