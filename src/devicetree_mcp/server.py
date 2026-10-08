"""devicetree-mcp: an MCP server that lets an AI agent read and explain devicetrees."""

from __future__ import annotations

import functools
import shlex
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import loader, render
from . import validate as valmod
from .fdt import FDTError
from .model import DTError, Tree, human_size

mcp = MCPServer(
    "devicetree",
    instructions=(
        "Read, explain and check devicetrees (.dtb, or .dts when dtc is installed) for "
        "embedded Linux, Zephyr, U-Boot and hobby OSes. Start with dt_load (a file, or "
        "qemu_arch='aarch64'/'riscv64' to get the DTB QEMU's -M virt hands a guest), then "
        "dt_tree for an overview, dt_node for one node with every property decoded, "
        "dt_memory_map / dt_interrupt_map for system-wide views, dt_validate to find "
        "mistakes. Nodes can be named by /path, &label, alias, or phandle:0xN."
    ),
)

_trees: dict[str, Tree] = {}

F = TypeVar("F", bound=Callable[..., Any])

# Failures an agent can act on. MCPServer only forwards a ToolError's message
# to the client - anything else arrives as a bare "Error executing tool X" -
# so these are re-raised as ToolError with their text intact.
_EXPECTED = (DTError, FDTError, KeyError, ValueError, FileNotFoundError)


def tool(fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except _EXPECTED as e:
            msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
            raise ToolError(str(msg)) from e

    mcp.tool()(wrapper)
    return fn


def _get(name: str) -> Tree:
    t = _trees.get(name)
    if t is None:
        known = ", ".join(sorted(_trees)) or "none"
        raise DTError(f"no devicetree loaded as {name!r} (loaded: {known}) - call dt_load first")
    return t


@tool
def dt_load(name: str, path: str | None = None, qemu_arch: str | None = None,
            qemu_machine: str = "virt", qemu_args: str | None = None,
            include_dirs: list[str] | None = None) -> str:
    """Load a devicetree under `name` for the other dt_* tools.

    Either `path` - a .dtb (parsed directly) or a .dts (compiled with dtc, if
    installed; include_dirs are passed as dtc -i) - or `qemu_arch` (e.g.
    "aarch64", "riscv64") to load the exact DTB that `qemu-system-<arch> -M
    <qemu_machine>` generates for its guest. qemu_args adds QEMU flags that
    change the tree, e.g. "-smp 4 -m 2G -cpu cortex-a72".
    """
    if (path is None) == (qemu_arch is None):
        raise ValueError("pass exactly one of path= (a .dtb/.dts file) or qemu_arch= (e.g. 'aarch64')")
    if path is not None:
        tree = loader.load_file(path, include_dirs)
    else:
        assert qemu_arch is not None
        tree = loader.load_qemu(qemu_arch, qemu_machine, shlex.split(qemu_args) if qemu_args else None)
    _trees[name] = tree
    nodes = tree.fdt.nodes()
    root_compat = render.prop_value(tree, tree.fdt.root, "compatible",
                                    tree.fdt.root.props.get("compatible", b""))
    model_txt = render.prop_value(tree, tree.fdt.root, "model", tree.fdt.root.props["model"]) \
        if "model" in tree.fdt.root.props else "(no model)"
    cpus = tree.by_path.get("/cpus")
    ncpu = sum(1 for c in cpus.children if c.base_name == "cpu") if cpus else 0
    mem = []
    for n in nodes:
        if n.props.get("device_type") == b"memory\x00":
            for r in tree.regions(n):
                if r.cpu_addr is not None:
                    mem.append(f"{r.cpu_addr:#x} ({human_size(r.size)})")
    findings = valmod.check(tree)
    errors = sum(1 for f in findings if f.severity == "error")
    return "\n".join([
        f"Loaded {name!r} from {tree.source}: FDT v{tree.fdt.version}, {len(nodes)} nodes, "
        f"{len(tree.by_phandle)} phandles, {len(tree.labels)} labels.",
        f"model: {model_txt}; compatible: {root_compat}",
        f"{ncpu} CPU(s); memory: {', '.join(mem) or 'none declared'}",
        f"dt_validate: {errors} error(s), {len(findings) - errors} warning(s).",
        "Next: dt_tree for an overview, or dt_memory_map / dt_interrupt_map.",
    ])


@tool
def dt_tree(name: str, path: str = "/", depth: int = 2) -> str:
    """Show the node hierarchy from `path` down `depth` levels, one line per
    node: what it is, where it's mapped, and its interrupts."""
    if not 0 <= depth <= 10:
        raise ValueError("depth must be between 0 and 10")
    t = _get(name)
    return render.tree_view(t, t.node(path), depth)


@tool
def dt_node(name: str, node: str) -> str:
    """Everything about one node: every property decoded (reg translated to CPU
    addresses, interrupts resolved to their controller, phandles to paths,
    clocks with frequencies), its labels, children, and who references it.
    node is a /path, &label, alias, or phandle:0xN."""
    t = _get(name)
    return render.node_detail(t, t.node(node))


@tool
def dt_find(name: str, compatible: str | None = None, label: str | None = None,
            phandle: str | None = None, property: str | None = None,
            node_name: str | None = None) -> str:
    """Find nodes. All given filters must match: compatible (substring, e.g.
    "pl011" or "virtio"), label, phandle ("0x8002"), property (has this
    property, e.g. "interrupt-controller"), node_name (substring)."""
    if all(x is None for x in (compatible, label, phandle, property, node_name)):
        raise ValueError("give at least one filter: compatible, label, phandle, property or node_name")
    t = _get(name)
    ph = int(phandle, 0) if phandle is not None else None
    return render.find(t, compatible, label, ph, property, node_name)


@tool
def dt_memory_map(name: str) -> str:
    """Every reg region translated through each bus's `ranges` to a CPU physical
    address, sorted, with overlaps flagged and /memreserve/ entries listed."""
    return render.memory_map(_get(name))


@tool
def dt_interrupt_map(name: str) -> str:
    """Interrupt routing: each controller, what feeds it, and every device
    interrupt decoded (e.g. GIC 'SPI 1 (INTID 33) level-high')."""
    return render.interrupt_map(_get(name))


@tool
def dt_validate(name: str) -> str:
    """Structural checks: reg/ranges sizes vs #address-cells/#size-cells,
    unit address vs reg, dangling or reserved phandles, interrupts without a
    routable parent or with the wrong cell count, duplicate unit addresses or
    phandles, and overlapping memory regions. (Not a binding/schema check.)"""
    findings = valmod.check(_get(name))
    if not findings:
        return "No structural problems found."
    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warning"]
    head = f"{len(errors)} error(s), {len(warns)} warning(s):"
    return "\n".join([head, *(str(f) for f in errors + warns)])


@tool
def dt_diff(a: str, b: str) -> str:
    """Compare two loaded trees: nodes added/removed and properties changed
    (decoded, so a reg change shows as addresses). Phandle renumbering is ignored."""
    return render.diff(_get(a), _get(b))


@tool
def dt_trees() -> str:
    """List loaded trees."""
    if not _trees:
        return "No trees loaded - call dt_load."
    return "\n".join(f"{n}: {t.source} ({len(t.by_path)} nodes)" for n, t in _trees.items())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
