"""Turn a Tree into text an agent can reason about.

The rule: never print raw cells when their meaning is knowable. `reg = <0x0
0x9000000 0x0 0x1000>` becomes "0x09000000 (4 KiB)"; an interrupt specifier
becomes "SPI 1 (INTID 33) level-high on /intc@8000000 (GICv2)".
"""

from __future__ import annotations

from .fdt import Node
from .model import (
    PHANDLE_PROPS,
    pci_unit,
    STRING_PROPS,
    DTError,
    Tree,
    compatibles,
    describe,
    human_size,
    strings,
    u32s,
)
from .model import interrupt_map as model_interrupt_map


def one_line(tree: Tree, n: Node) -> str:
    """`pl011@9000000: PL011 UART at 0x9000000 (4 KiB), IRQ SPI 1 ... on /intc@8000000`."""
    parts = []
    what = describe(n)
    comp = compatibles(n)
    if what:
        parts.append(what)
    elif comp:
        parts.append(comp[0])
    if strings(n.props.get("status", b"")) == ["disabled"]:
        parts.append("[disabled]")
    try:
        regs = tree.regions(n)
    except DTError as e:
        regs = []
        parts.append(f"(bad reg: {e})")
    mapped = [r for r in regs if r.cpu_addr is not None and r.size]
    if mapped:
        locs = ", ".join(f"{r.cpu_addr:#x} ({human_size(r.size)})" for r in mapped[:3])
        if len(mapped) > 3:
            locs += f", +{len(mapped) - 3} more"
        parts.append("at " + locs)
    try:
        irqs = tree.interrupts(n)
    except DTError as e:
        irqs = []
        parts.append(f"(bad interrupts: {e})")
    if irqs:
        ctrl = tree.by_path.get(irqs[0].controller or "")
        cdesc = f" ({describe(ctrl)})" if ctrl is not None and describe(ctrl) else ""
        irq_txt = "; ".join(i.meaning for i in irqs[:3])
        if len(irqs) > 3:
            irq_txt += f"; +{len(irqs) - 3} more"
        parts.append(f"IRQ {irq_txt} on {irqs[0].controller}{cdesc}")
    name = n.name or "/"
    return f"{name}: " + (", ".join(parts) if parts else "(no compatible, reg or interrupts)")


def tree_view(tree: Tree, start: Node, depth: int) -> str:
    lines: list[str] = []

    def rec(n: Node, level: int) -> None:
        lines.append("  " * level + one_line(tree, n))
        if level < depth:
            for group in _sibling_runs(n.children):
                if len(group) >= 4:
                    lines.append("  " * (level + 1) + _run_summary(tree, group))
                else:
                    for c in group:
                        rec(c, level + 1)
        elif n.children:
            lines.append("  " * (level + 1) + f"... {len(n.children)} children (raise depth)")

    rec(start, 0)
    return "\n".join(lines)


def _sibling_runs(children: list[Node]) -> list[list[Node]]:
    """Group consecutive childless siblings that are the same kind of device
    (same base name and compatible), e.g. QEMU's 32 virtio_mmio slots."""
    runs: list[list[Node]] = []
    for c in children:
        key = (c.base_name, c.props.get("compatible"))
        if (runs and not c.children and not runs[-1][-1].children
                and (runs[-1][-1].base_name, runs[-1][-1].props.get("compatible")) == key):
            runs[-1].append(c)
        else:
            runs.append([c])
    return runs


def _run_summary(tree: Tree, group: list[Node]) -> str:
    first, last = group[0], group[-1]
    what = describe(first) or (compatibles(first)[0] if compatibles(first) else first.base_name)
    txt = f"{first.name} .. {last.name}: {len(group)} x {what}"
    try:
        a, b = tree.regions(first), tree.regions(last)
        if a and b and a[0].cpu_addr is not None and b[0].cpu_addr is not None:
            txt += f", at {a[0].cpu_addr:#x} .. {b[0].cpu_addr:#x} ({human_size(a[0].size)} each)"
        ia, ib = tree.interrupts(first), tree.interrupts(last)
        if ia and ib:
            txt += f", IRQ {ia[0].meaning} .. {ib[0].meaning} on {ia[0].controller}"
    except DTError:
        pass
    return txt + " (dt_node any one for detail)"


def prop_value(tree: Tree, n: Node, key: str, v: bytes) -> str:
    """Decode one property for display."""
    if not v:
        return "(empty - a boolean flag)"
    if key in STRING_PROPS or key.endswith("-names"):
        s = strings(v)
        if s is not None:
            return ", ".join(f'"{x}"' for x in s)
    if key == "reg":
        try:
            regs = tree.regions(n)
        except DTError as e:
            return f"<{v.hex()}> - {e}"
        out = []
        for r in regs:
            if r.cpu_addr is None:
                out.append(f"{r.child_addr:#x}" + (f" size {r.size:#x}" if r.size else "")
                           + (f" ({r.note})" if r.note and r.size else ""))
            else:
                t = f"{r.cpu_addr:#x} size {r.size:#x} ({human_size(r.size)})" if r.size else f"{r.cpu_addr:#x}"
                if r.cpu_addr != r.child_addr:
                    t += f" [bus address {r.child_addr:#x}{r.note}]"
                out.append(t)
        return "; ".join(out)
    if key == "ranges":
        try:
            rng = tree.ranges(n)
        except DTError as e:
            return f"<{v.hex()}> - {e}"
        if rng == []:
            return "(empty - child addresses are identical to the parent's)"
        assert rng is not None
        return "; ".join(f"child {c:#x} -> parent {p:#x} size {human_size(s)}" for c, p, s in rng)
    if key in ("interrupts", "interrupts-extended"):
        try:
            return "; ".join(f"{i.meaning} on {i.controller}" for i in tree.interrupts(n))
        except DTError as e:
            return f"<{v.hex()}> - {e}"
    if len(v) % 4 == 0:
        cells = u32s(v)
        if key in PHANDLE_PROPS:
            return _phandle_list(tree, key, cells)
        if key in ("phandle", "linux,phandle"):
            return f"{cells[0]:#x} (this node's handle)"
        if len(cells) <= 8:
            txt = " ".join(f"{c:#x}" for c in cells)
            return f"<{txt}>" + (f" = {cells[0]}" if len(cells) == 1 and cells[0] < 1 << 20 else "")
        return f"<{len(cells)} cells: {' '.join(f'{c:#x}' for c in cells[:8])} ...>"
    s = strings(v)
    if s is not None:
        return ", ".join(f'"{x}"' for x in s)
    return f"[{len(v)} bytes: {v[:32].hex(' ')}{' ...' if len(v) > 32 else ''}]"


def _phandle_list(tree: Tree, key: str, cells: list[int]) -> str:
    cells_prop = {"clocks": "#clock-cells", "resets": "#reset-cells", "phys": "#phy-cells",
                  "dmas": "#dma-cells", "iommus": "#iommu-cells",
                  "power-domains": "#power-domain-cells"}.get(key)
    out = []
    i = 0
    while i < len(cells):
        ph = cells[i]
        target = tree.phandle_target(ph)
        if target is None:
            out.append(f"<{ph:#x}> (no node has this phandle!)")
            i += 1
            continue
        n_args = 0
        if cells_prop and cells_prop in target.props:
            n_args = u32s(target.props[cells_prop])[0]
        args = cells[i + 1:i + 1 + n_args]
        t = target.path + (f" args {' '.join(hex(a) for a in args)}" if args else "")
        if key == "clocks" and "clock-frequency" in target.props:
            hz = u32s(target.props["clock-frequency"])[0]
            t += f" ({hz / 1e6:g} MHz)"
        out.append(t)
        i += 1 + n_args
    return "; ".join(out)


def node_detail(tree: Tree, n: Node) -> str:
    lines = [one_line(tree, n), f"path: {n.path}"]
    labels = [lbl for lbl, p in tree.labels.items() if p == n.path]
    if labels:
        lines.append("labels: " + ", ".join("&" + lbl for lbl in labels))
    aliases = [a for a, p in tree.aliases.items() if p == n.path]
    if aliases:
        lines.append("aliases: " + ", ".join(aliases))
    if n.parent is not None:
        ac, sc = tree.address_cells(n.parent), tree.size_cells(n.parent)
        lines.append(f"reg encoding (from parent {n.parent.path}): #address-cells={ac} #size-cells={sc}")
    ip = tree.interrupt_parent(n)
    if ip is not None and "interrupt-parent" not in n.props and ("interrupts" in n.props):
        lines.append(f"interrupt-parent (inherited): {ip.path}")
    lines.append("properties:")
    for k, v in n.props.items():
        lines.append(f"  {k} = {prop_value(tree, n, k, v)}")
    if n.children:
        lines.append(f"children ({len(n.children)}): " + ", ".join(c.name for c in n.children[:30])
                     + (" ..." if len(n.children) > 30 else ""))
    # Who points at this node?
    refs = referrers(tree, n)
    if refs:
        lines.append("referenced by: " + "; ".join(refs[:15]) + (" ..." if len(refs) > 15 else ""))
    return "\n".join(lines)


def referrers(tree: Tree, target: Node) -> list[str]:
    ph = None
    for key in ("phandle", "linux,phandle"):
        if key in target.props:
            ph = u32s(target.props[key])[0]
    if ph is None:
        return []
    out = []
    for n in tree.fdt.nodes():
        for k, v in n.props.items():
            if k in ("phandle", "linux,phandle") or len(v) % 4 or not v:
                continue
            if k in PHANDLE_PROPS and ph in u32s(v):
                out.append(f"{n.path} ({k})")
            elif k == "interrupt-map" and ph in u32s(v):
                out.append(f"{n.path} (interrupt-map)")
    return out


def memory_map(tree: Tree) -> str:
    rows = []
    unmapped = []
    for n in tree.fdt.nodes():
        if "reg" not in n.props or n.path.startswith("/cpus"):
            continue
        try:
            regs = tree.regions(n)
        except DTError as e:
            unmapped.append(f"{n.path}: {e}")
            continue
        for r in regs:
            if r.cpu_addr is None:
                if r.size:
                    unmapped.append(f"{n.path}[{r.index}] {r.child_addr:#x}: {r.note}")
                continue
            if r.size:
                rows.append((r.cpu_addr, r.size, n, r.index))
    rows.sort(key=lambda x: x[0])
    lines = ["CPU physical address map (reg translated through every bus `ranges`):"]
    reach = -1
    reach_node = ""
    for addr, size, n, idx in rows:
        end = addr + size - 1
        mark = ""
        if addr <= reach and not (n.path.startswith(reach_node + "/") or reach_node.startswith(n.path + "/")):
            mark = f"   !! OVERLAPS {reach_node}"
        what = describe(n) or (compatibles(n)[0] if compatibles(n) else "")
        reg_names = strings(n.props.get("reg-names", b""))
        rn = f" [{reg_names[idx]}]" if reg_names and idx < len(reg_names) else (f" [reg {idx}]" if idx else "")
        lines.append(f"  {addr:#014x}-{end:#014x} {human_size(size):>9}  {n.path}{rn}  {what}{mark}")
        if end > reach:
            reach, reach_node = end, n.path
    for addr, size in tree.fdt.reservations:
        lines.append(f"  {addr:#014x}-{addr + size - 1:#014x} {human_size(size):>9}  (/memreserve/ - firmware-reserved)")
    if unmapped:
        lines.append("Not CPU-addressable:")
        lines.extend("  " + u for u in unmapped)
    return "\n".join(lines)


def interrupt_map(tree: Tree) -> str:
    controllers: dict[str, list[tuple[list[int], str]]] = {}
    unrouted = []
    for n in tree.fdt.nodes():
        try:
            irqs = tree.interrupts(n)
        except DTError as e:
            unrouted.append(f"{n.path}: {e}")
            continue
        for i in irqs:
            if i.controller is None:
                unrouted.append(f"{n.path}: {i.meaning}")
            else:
                controllers.setdefault(i.controller, []).append((i.specifier, f"{i.meaning:<40} {n.path}"))
    lines = []
    for ctrl_path in sorted(controllers):
        ctrl = tree.by_path[ctrl_path]
        d = describe(ctrl)
        upstream = ""
        if "interrupts" in ctrl.props or "interrupts-extended" in ctrl.props:
            try:
                ups = sorted({u.controller for u in tree.interrupts(ctrl) if u.controller})
                if ups:
                    shown = ", ".join(ups[:4]) + (f" +{len(ups) - 4} more" if len(ups) > 4 else "")
                    upstream = f"  -> feeds {shown}"
            except DTError:
                pass
        lines.append(f"{ctrl_path}" + (f" ({d})" if d else "") + upstream)
        for _, entry in sorted(controllers[ctrl_path]):
            lines.append(f"  {entry}")
    for n in tree.fdt.nodes():
        if "interrupt-map" not in n.props:
            continue
        try:
            mask, entries = model_interrupt_map(tree, n)
        except DTError as err:
            unrouted.append(str(err))
            continue
        is_pci = tree.cells_of(n, "#address-cells", 0) == 3
        lines.append(f"{n.path} interrupt-map ({len(entries)} entries"
                     + (f", mask {' '.join(hex(m) for m in mask)}" if mask else "") + "):")
        if is_pci:
            lines.append("  PCI INTx routing (device slot + pin -> parent interrupt). The mask keeps "
                         "only the device number bits, so this pattern repeats for every bus; the "
                         "pin rotates by slot (the standard 'swizzle').")
        for ent in entries:
            if is_pci and ent.child_spec:
                pin = "INT" + "ABCD"[(ent.child_spec[0] - 1) % 4] if 1 <= ent.child_spec[0] <= 4 else f"pin {ent.child_spec[0]}"
                child = f"dev {pci_unit(ent.child_unit)} {pin}"
            else:
                child = f"unit {' '.join(hex(c) for c in ent.child_unit)} spec {' '.join(hex(c) for c in ent.child_spec)}"
            lines.append(f"  {child:<22} -> {ent.meaning} on {ent.controller}")
    if unrouted:
        lines.append("Unroutable:")
        lines.extend("  " + u for u in unrouted)
    return "\n".join(lines) if lines else "No interrupts in this tree."


def find(tree: Tree, compatible: str | None, label: str | None, phandle: int | None,
         prop: str | None, name: str | None) -> str:
    hits: list[Node] = []
    for n in tree.fdt.nodes():
        if compatible is not None and not any(compatible.lower() in c.lower() for c in compatibles(n)):
            continue
        if prop is not None and prop not in n.props:
            continue
        if name is not None and name.lower() not in n.name.lower():
            continue
        if phandle is not None and tree.by_phandle.get(phandle) is not n:
            continue
        if label is not None and tree.labels.get(label) != n.path:
            continue
        hits.append(n)
    if not hits:
        return "No matching nodes."
    out = [f"{len(hits)} match(es):"]
    for n in hits[:50]:
        out.append(f"  {n.path} - {one_line(tree, n).split(': ', 1)[1]}")
    if len(hits) > 50:
        out.append(f"  ... {len(hits) - 50} more (narrow the query)")
    return "\n".join(out)


def diff(a: Tree, b: Tree) -> str:
    pa, pb = set(a.by_path), set(b.by_path)
    out = []
    for p in sorted(pa - pb):
        out.append(f"- {p}  (only in {a.source})")
    for p in sorted(pb - pa):
        out.append(f"+ {p}  (only in {b.source})  {one_line(b, b.by_path[p]).split(': ', 1)[1]}")
    for p in sorted(pa & pb):
        na, nb = a.by_path[p], b.by_path[p]
        for k in sorted(set(na.props) | set(nb.props)):
            va, vb = na.props.get(k), nb.props.get(k)
            if va == vb:
                continue
            if k in ("phandle", "linux,phandle"):
                continue  # renumbering is noise
            if va is None:
                assert vb is not None
                out.append(f"~ {p}: + {k} = {prop_value(b, nb, k, vb)}")
            elif vb is None:
                out.append(f"~ {p}: - {k} (was {prop_value(a, na, k, va)})")
            else:
                ta, tb = prop_value(a, na, k, va), prop_value(b, nb, k, vb)
                if ta != tb:
                    out.append(f"~ {p}: {k}: {ta}  ->  {tb}")
    return "\n".join(out) if out else "The trees are identical (ignoring phandle numbering)."
