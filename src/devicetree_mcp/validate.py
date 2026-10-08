"""Structural checks that don't need binding schemas.

Each check is something that has actually broken a real board bring-up: a
node whose reg doesn't match its parent's cell sizes, a phandle pointing at
nothing, a unit address that disagrees with reg. Findings carry a severity
and say what to change.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fdt import Node
from .model import DTError, Region, Tree, u32s


@dataclass(frozen=True)
class Finding:
    severity: str   # "error" | "warning"
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity}: {self.path}: {self.message}"


def check(tree: Tree) -> list[Finding]:
    out: list[Finding] = []
    nodes = tree.fdt.nodes()
    for n in nodes:
        if n.path.startswith(("/__symbols__", "/__fixups__", "/__local_fixups__")):
            continue
        out += _reg(tree, n)
        out += _unit_address(tree, n)
        out += _phandle_refs(tree, n)
        out += _interrupts(tree, n)
        out += _ranges(tree, n)
    out += _duplicate_unit_addresses(nodes)
    out += _duplicate_phandles(tree)
    out += _overlaps(tree)
    return out


def _reg(tree: Tree, n: Node) -> list[Finding]:
    if "reg" not in n.props:
        return []
    try:
        tree.reg(n)
    except DTError as e:
        return [Finding("error", n.path, str(e).split(": ", 1)[-1])]
    return []


def _unit_address(tree: Tree, n: Node) -> list[Finding]:
    ua = n.unit_address
    has_reg = "reg" in n.props
    if ua is None:
        if has_reg and n.parent is not None and n.parent.path != "/cpus":
            return [Finding("warning", n.path, "has reg but no unit address in its name "
                            "(expected name@<first reg address>)")]
        return []
    if not has_reg and "ranges" not in n.props:
        return [Finding("warning", n.path, f"has unit address @{ua} but no reg or ranges property")]
    if not has_reg:
        return []
    try:
        regs = tree.reg(n)
    except DTError:
        return []  # already reported by _reg
    if not regs:
        return []
    first = regs[0][0]
    # Unit addresses are written in hex without 0x; some buses use "a,b" forms.
    if "," in ua:
        return []
    try:
        val = int(ua, 16)
    except ValueError:
        return [Finding("warning", n.path, f"unit address @{ua} is not hex")]
    if val != first:
        return [Finding("warning", n.path, f"unit address @{ua} does not match the first reg "
                        f"address {first:#x} - rename the node to {n.base_name}@{first:x}")]
    return []


def _phandle_refs(tree: Tree, n: Node) -> list[Finding]:
    out = []
    for prop in ("interrupt-parent", "msi-parent", "memory-region", "next-level-cache"):
        if prop in n.props and len(n.props[prop]) >= 4:
            ph = u32s(n.props[prop][:4])[0]
            if ph in (0, 0xFFFFFFFF):
                out.append(Finding("error", n.path, f"{prop} = <{ph:#x}>: phandle values 0 and "
                                   f"0xffffffff are reserved by the spec and never name a node"))
            elif tree.phandle_target(ph) is None:
                out.append(Finding("error", n.path, f"{prop} points at phandle {ph:#x}, "
                                   f"which no node has (dangling reference)"))
    if "clocks" in n.props:
        out += _cell_list_refs(tree, n, "clocks", "#clock-cells")
    return out


def _cell_list_refs(tree: Tree, n: Node, prop: str, cells_prop: str) -> list[Finding]:
    try:
        cells = u32s(n.props[prop])
    except DTError as e:
        return [Finding("error", n.path, f"{prop}: {e}")]
    i = 0
    while i < len(cells):
        target = tree.phandle_target(cells[i])
        if target is None:
            return [Finding("error", n.path, f"{prop}[{i}] points at phandle {cells[i]:#x}, "
                            f"which no node has (dangling reference)")]
        if cells_prop not in target.props:
            return [Finding("error", n.path, f"{prop} references {target.path}, which has no "
                            f"{cells_prop} - can't tell how many argument cells follow")]
        i += 1 + u32s(target.props[cells_prop])[0]
    if i != len(cells):
        return [Finding("error", n.path, f"{prop} ends mid-specifier ({len(cells)} cells)")]
    return []


def _interrupts(tree: Tree, n: Node) -> list[Finding]:
    if "interrupts" not in n.props:
        return []
    ctrl = tree.interrupt_parent(n)
    if ctrl is None:
        return [Finding("error", n.path, "has interrupts but no interrupt-parent on it or any "
                        "ancestor - the interrupts can't be routed")]
    if "#interrupt-cells" not in ctrl.props:
        return [Finding("error", n.path, f"interrupt parent {ctrl.path} has no #interrupt-cells")]
    k = u32s(ctrl.props["#interrupt-cells"])[0]
    count = len(n.props["interrupts"]) // 4
    if k and count % k:
        return [Finding("error", n.path, f"interrupts has {count} cells, not a multiple of "
                        f"{ctrl.path}'s #interrupt-cells = {k}")]
    if "interrupt-controller" not in ctrl.props and "interrupt-map" not in ctrl.props:
        return [Finding("warning", n.path, f"interrupt parent {ctrl.path} is neither an "
                        f"interrupt-controller nor has an interrupt-map")]
    return []


def _ranges(tree: Tree, n: Node) -> list[Finding]:
    if "ranges" not in n.props:
        return []
    try:
        tree.ranges(n)
    except DTError as e:
        return [Finding("error", n.path, str(e).split(": ", 1)[-1])]
    return []


def _duplicate_unit_addresses(nodes: list[Node]) -> list[Finding]:
    out = []
    for n in nodes:
        seen: dict[str, str] = {}
        for c in n.children:
            if c.unit_address is None:
                continue
            key = c.unit_address.lower().lstrip("0") or "0"
            if key in seen:
                out.append(Finding("warning", c.path, f"same unit address as {seen[key]} "
                                   f"under {n.path or '/'}"))
            else:
                seen[key] = c.path
    return out


def _duplicate_phandles(tree: Tree) -> list[Finding]:
    seen: dict[int, str] = {}
    out = []
    for n in tree.fdt.nodes():
        if "phandle" in n.props and len(n.props["phandle"]) == 4:
            ph = u32s(n.props["phandle"])[0]
            if ph in seen:
                out.append(Finding("error", n.path, f"phandle {ph:#x} is also used by {seen[ph]}"))
            seen[ph] = n.path
    return out


def _overlaps(tree: Tree) -> list[Finding]:
    regions = []
    for n in tree.fdt.nodes():
        if n.path.startswith("/cpus") or "reg" not in n.props:
            continue
        try:
            for r in tree.regions(n):
                if r.cpu_addr is not None and r.size:
                    regions.append(r)
        except DTError:
            continue
    regions.sort(key=lambda r: r.cpu_addr or 0)
    out = []
    # Compare each region with the one reaching furthest so far, so a large
    # region overlapping several later ones is caught, not just neighbours.
    widest: Region | None = None
    for r in regions:
        assert r.cpu_addr is not None
        if widest is not None and widest.cpu_addr is not None:
            related = (r.node == widest.node or r.node.startswith(widest.node + "/")
                       or widest.node.startswith(r.node + "/"))
            if r.cpu_addr < widest.cpu_addr + widest.size and not related:
                out.append(Finding("error", r.node, f"reg [{r.cpu_addr:#x}, +{r.size:#x}) overlaps "
                                   f"{widest.node} [{widest.cpu_addr:#x}, +{widest.size:#x})"))
        if widest is None or widest.cpu_addr is None or \
                r.cpu_addr + r.size > widest.cpu_addr + widest.size:
            widest = r
    return out

