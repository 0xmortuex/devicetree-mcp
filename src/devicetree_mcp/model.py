"""Meaning on top of the raw tree: cells, phandles, labels, reg, ranges, interrupts.

The rules all come from the Devicetree Specification v0.4:

- A node's `reg` is encoded with its PARENT's #address-cells / #size-cells
  (defaults 2 and 1 when the parent omits them, §2.3.5).
- `ranges` on a bus maps child addresses to parent addresses. An empty
  `ranges` is identity; a missing one means the children are not memory-
  mapped in the parent's space (§2.3.8).
- `interrupt-parent` is inherited from the nearest ancestor that has one
  (§2.4.1); `interrupts-extended` names the controller per entry.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .fdt import FDT, Node

DEFAULT_ADDRESS_CELLS = 2
DEFAULT_SIZE_CELLS = 1

# Properties whose value is a list of strings.
STRING_PROPS = {
    "compatible", "model", "status", "device_type", "name", "clock-names",
    "clock-output-names", "reg-names", "interrupt-names", "dma-names", "reset-names",
    "pinctrl-names", "phy-names", "bootargs", "stdout-path", "stdin-path", "method",
    "enable-method", "label", "riscv,isa", "riscv,isa-base", "riscv,isa-extensions",
    "mmu-type", "entry-method", "power-domain-names", "gpio-line-names",
}
# Properties whose cells start with a phandle.
PHANDLE_PROPS = {
    "interrupt-parent", "clocks", "msi-parent", "resets", "power-domains", "phys",
    "dmas", "iommus", "cpu-idle-states", "next-level-cache", "pinctrl-0", "pinctrl-1",
    "regmap", "syscon", "interrupts-extended", "memory-region", "cpu",
}


class DTError(ValueError):
    pass


def u32s(v: bytes) -> list[int]:
    if len(v) % 4:
        raise DTError(f"value of {len(v)} bytes is not a whole number of 32-bit cells")
    return list(struct.unpack(f">{len(v) // 4}I", v))


def cells_to_int(cells: list[int]) -> int:
    out = 0
    for c in cells:
        out = (out << 32) | c
    return out


def strings(v: bytes) -> list[str] | None:
    """Decode a NUL-separated string list, or None if it doesn't look like one."""
    if not v or v[-1] != 0:
        return None
    parts = v[:-1].split(b"\0")
    if any(not p for p in parts):
        return None
    try:
        out = [p.decode("ascii") for p in parts]
    except UnicodeDecodeError:
        return None
    if not all(s.isprintable() for s in out):
        return None
    return out


@dataclass(frozen=True)
class Region:
    node: str          # path
    index: int         # which reg entry
    child_addr: int    # address as written in reg
    cpu_addr: int | None   # translated through every ranges, None if not mapped
    size: int
    note: str = ""


@dataclass(frozen=True)
class Interrupt:
    node: str
    controller: str | None   # path of the interrupt controller
    specifier: list[int]
    meaning: str


class Tree:
    """A parsed FDT plus lookups (phandles, labels, aliases)."""

    def __init__(self, fdt: FDT, source: str):
        self.fdt = fdt
        self.source = source
        self.by_path: dict[str, Node] = {n.path: n for n in fdt.nodes()}
        self.by_phandle: dict[int, Node] = {}
        for n in fdt.nodes():
            for key in ("phandle", "linux,phandle"):
                if key in n.props and len(n.props[key]) == 4:
                    self.by_phandle[u32s(n.props[key])[0]] = n
        self.labels: dict[str, str] = {}
        sym = self.by_path.get("/__symbols__")
        if sym is not None:
            for k, v in sym.props.items():
                s = strings(v)
                if s:
                    self.labels[k] = s[0]
        self.aliases: dict[str, str] = {}
        al = self.by_path.get("/aliases")
        if al is not None:
            for k, v in al.props.items():
                s = strings(v)
                if s:
                    self.aliases[k] = s[0]

    # -- lookup ----------------------------------------------------------------

    def node(self, ref: str) -> Node:
        """Resolve "/path", "&label", "label", an alias, or "phandle:0x8002"."""
        r = ref.strip()
        if r.startswith("&"):
            r = r[1:]
            if r not in self.labels:
                raise DTError(f"no label {r!r}; labels come from /__symbols__ (dtc -@). "
                              f"Known: {', '.join(sorted(self.labels)[:20]) or 'none'}")
            return self.node(self.labels[r])
        if r.lower().startswith("phandle:"):
            ph = int(r.split(":", 1)[1], 0)
            if ph not in self.by_phandle:
                raise DTError(f"no node has phandle {ph:#x}")
            return self.by_phandle[ph]
        if r.startswith("/"):
            if r in self.by_path:
                return self.by_path[r]
            node = self.fdt.root
            for part in [p for p in r.split("/") if p]:
                nxt = node.child(part)
                if nxt is None:
                    names = ", ".join(c.name for c in node.children[:25])
                    raise DTError(f"{node.path} has no child {part!r}. Children: {names}")
                node = nxt
            return node
        if r in self.labels:
            return self.node(self.labels[r])
        if r in self.aliases:
            return self.node(self.aliases[r])
        raise DTError(f"can't resolve {ref!r}: use a /path, &label, alias, or phandle:0xN")

    def phandle_target(self, ph: int) -> Node | None:
        return self.by_phandle.get(ph)

    # -- cells -----------------------------------------------------------------

    @staticmethod
    def cells_of(node: Node | None, prop: str, default: int) -> int:
        if node is None or prop not in node.props:
            return default
        return u32s(node.props[prop])[0]

    def address_cells(self, bus: Node | None) -> int:
        return self.cells_of(bus, "#address-cells", DEFAULT_ADDRESS_CELLS)

    def size_cells(self, bus: Node | None) -> int:
        return self.cells_of(bus, "#size-cells", DEFAULT_SIZE_CELLS)

    # -- reg / ranges ----------------------------------------------------------

    def reg(self, node: Node) -> list[tuple[int, int]]:
        if "reg" not in node.props:
            return []
        ac, sc = self.address_cells(node.parent), self.size_cells(node.parent)
        cells = u32s(node.props["reg"])
        stride = ac + sc
        if stride == 0 or len(cells) % stride:
            raise DTError(
                f"{node.path}: reg has {len(cells)} cells, not a multiple of "
                f"#address-cells({ac}) + #size-cells({sc}) = {stride} from {node.parent.path if node.parent else '/'}"
            )
        out = []
        for i in range(0, len(cells), stride):
            out.append((cells_to_int(cells[i:i + ac]), cells_to_int(cells[i + ac:i + stride])))
        return out

    def ranges(self, bus: Node) -> list[tuple[int, int, int]] | None:
        """(child_addr, parent_addr, size) entries; [] = identity; None = no ranges."""
        if "ranges" not in bus.props:
            return None
        v = bus.props["ranges"]
        if not v:
            return []
        cac = self.address_cells(bus)
        pac = self.address_cells(bus.parent)
        csc = self.size_cells(bus)
        cells = u32s(v)
        stride = cac + pac + csc
        if len(cells) % stride:
            raise DTError(f"{bus.path}: ranges has {len(cells)} cells, not a multiple of "
                          f"child({cac}) + parent({pac}) + size({csc}) = {stride}")
        out = []
        for i in range(0, len(cells), stride):
            child = cells[i:i + cac]
            # PCI child addresses carry a phys.hi flags cell; only the low 64 bits are an address.
            caddr = cells_to_int(child[1:] if cac == 3 else child)
            out.append((caddr, cells_to_int(cells[i + cac:i + cac + pac]),
                        cells_to_int(cells[i + cac + pac:i + stride])))
        return out

    def translate(self, node: Node, addr: int) -> tuple[int | None, str]:
        """Translate an address in node's parent's space up to the CPU's."""
        bus = node.parent
        hops = []
        while bus is not None and bus.parent is not None:
            rng = self.ranges(bus)
            if rng is None:
                return None, f"{bus.path} has no `ranges`, so its children aren't CPU-addressable"
            if rng:
                for c, p, s in rng:
                    if c <= addr < c + s:
                        addr = addr - c + p
                        hops.append(bus.path)
                        break
                else:
                    return None, f"{addr:#x} is outside every `ranges` window of {bus.path}"
            bus = bus.parent
        return addr, (" via " + ", ".join(hops)) if hops else ""

    def regions(self, node: Node) -> list[Region]:
        out = []
        for i, (a, s) in enumerate(self.reg(node)):
            cpu, note = self.translate(node, a)
            out.append(Region(node.path, i, a, cpu, s, note))
        return out

    # -- interrupts --------------------------------------------------------------

    def interrupt_parent(self, node: Node) -> Node | None:
        n: Node | None = node
        while n is not None:
            if "interrupt-parent" in n.props:
                return self.phandle_target(u32s(n.props["interrupt-parent"])[0])
            n = n.parent
        return None

    def interrupts(self, node: Node) -> list[Interrupt]:
        out: list[Interrupt] = []
        if "interrupts-extended" in node.props:
            cells = u32s(node.props["interrupts-extended"])
            i = 0
            while i < len(cells):
                ctrl = self.phandle_target(cells[i])
                if ctrl is None:
                    raise DTError(f"{node.path}: interrupts-extended references unknown phandle {cells[i]:#x}")
                n = self.cells_of(ctrl, "#interrupt-cells", 1)
                spec = cells[i + 1:i + 1 + n]
                out.append(Interrupt(node.path, ctrl.path, spec, describe_irq(ctrl, spec)))
                i += 1 + n
            return out
        if "interrupts" not in node.props:
            return out
        ctrl = self.interrupt_parent(node)
        cells = u32s(node.props["interrupts"])
        if ctrl is None:
            return [Interrupt(node.path, None, cells,
                              "no interrupt-parent anywhere up the tree - can't decode")]
        n = self.cells_of(ctrl, "#interrupt-cells", 0)
        if n == 0:
            return [Interrupt(node.path, ctrl.path, cells,
                              f"{ctrl.path} has no #interrupt-cells - can't split specifiers")]
        for i in range(0, len(cells) - n + 1, n):
            spec = cells[i:i + n]
            out.append(Interrupt(node.path, ctrl.path, spec, describe_irq(ctrl, spec)))
        return out


@dataclass(frozen=True)
class MapEntry:
    child_unit: list[int]     # child unit address (PCI: phys.hi, mid, lo)
    child_spec: list[int]     # child interrupt specifier (PCI: pin 1-4 = INTA-D)
    controller: str           # parent interrupt controller path
    parent_spec: list[int]
    meaning: str              # decoded parent specifier


def interrupt_map(tree: Tree, node: Node) -> tuple[list[int], list[MapEntry]]:
    """Decode `interrupt-map` (§2.4.3.1): (mask, entries).

    Each entry is child-unit-address (#address-cells of node), child
    specifier (#interrupt-cells of node), parent phandle, parent unit
    address (the PARENT's #address-cells, 0 if it has none - as Linux's
    of_irq_parse_raw does), parent specifier (parent's #interrupt-cells).
    """
    if "interrupt-map" not in node.props:
        return [], []
    cells = u32s(node.props["interrupt-map"])
    cac = tree.cells_of(node, "#address-cells", 0)
    cic = tree.cells_of(node, "#interrupt-cells", 0)
    if cic == 0:
        raise DTError(f"{node.path}: interrupt-map needs #interrupt-cells on the same node")
    mask = u32s(node.props["interrupt-map-mask"]) if "interrupt-map-mask" in node.props else []
    out = []
    i = 0
    while i < len(cells):
        unit = cells[i:i + cac]
        spec = cells[i + cac:i + cac + cic]
        i += cac + cic
        if i >= len(cells):
            raise DTError(f"{node.path}: interrupt-map ends mid-entry")
        ctrl = tree.phandle_target(cells[i])
        if ctrl is None:
            raise DTError(f"{node.path}: interrupt-map references unknown phandle {cells[i]:#x}")
        i += 1
        pac = tree.cells_of(ctrl, "#address-cells", 0)
        pic = tree.cells_of(ctrl, "#interrupt-cells", 0)
        i += pac
        pspec = cells[i:i + pic]
        i += pic
        out.append(MapEntry(unit, spec, ctrl.path, pspec, describe_irq(ctrl, pspec)))
    return mask, out


def pci_unit(unit: list[int]) -> str:
    """phys.hi of a PCI unit address -> bus:dev.fn (IEEE 1275 PCI binding)."""
    hi = unit[0] if unit else 0
    return f"{(hi >> 16) & 0xFF:02x}:{(hi >> 11) & 0x1F:02x}.{(hi >> 8) & 0x7}"


def compatibles(node: Node) -> list[str]:
    return strings(node.props.get("compatible", b"")) or []


def is_gic(node: Node) -> bool:
    return any(c.startswith(("arm,gic", "arm,cortex-a15-gic", "arm,cortex-a9-gic"))
               for c in compatibles(node))


_GIC_FLAGS = {1: "edge-rising", 2: "edge-falling", 3: "edge-both", 4: "level-high", 8: "level-low"}


def describe_irq(ctrl: Node, spec: list[int]) -> str:
    comp = compatibles(ctrl)
    if is_gic(ctrl) and len(spec) >= 3:
        kind = {0: "SPI", 1: "PPI", 2: "ESPI", 3: "EPPI"}.get(spec[0], f"type {spec[0]}")
        intid = spec[1] + (32 if spec[0] == 0 else 16 if spec[0] == 1 else 0)
        trig = _GIC_FLAGS.get(spec[2] & 0xF, f"flags {spec[2]:#x}")
        cpus = (spec[2] >> 8) & 0xFF
        extra = f", CPU mask {cpus:#04x}" if spec[0] == 1 and cpus else ""
        return f"{kind} {spec[1]} (INTID {intid}) {trig}{extra}"
    if any(c in ("riscv,cpu-intc",) for c in comp) and spec:
        names = {1: "supervisor software", 3: "machine software", 5: "supervisor timer",
                 7: "machine timer", 9: "supervisor external", 11: "machine external"}
        return f"hart-local cause {spec[0]} ({names.get(spec[0], 'interrupt')})"
    if any(c.endswith(("plic0", ",plic")) or "plic" in c for c in comp) and spec:
        return f"PLIC source {spec[0]}"
    if spec and len(spec) == 2:
        sense = {1: "edge-rising", 2: "edge-falling", 4: "level-high", 8: "level-low"}.get(spec[1])
        if sense:
            return f"line {spec[0]} {sense}"
    return "cells " + " ".join(f"{c:#x}" for c in spec)


# What a compatible string IS, for explanations. Deliberately short: the point
# is to orient the reader, not replace the binding docs.
KNOWN_DEVICES: dict[str, str] = {
    "arm,pl011": "PL011 UART",
    "arm,pl031": "PL031 real-time clock",
    "arm,pl061": "PL061 GPIO controller",
    "arm,primecell": "ARM PrimeCell peripheral",
    "arm,cortex-a15-gic": "GICv2 interrupt controller",
    "arm,gic-400": "GIC-400 (GICv2) interrupt controller",
    "arm,gic-v3": "GICv3 interrupt controller",
    "arm,gic-v3-its": "GICv3 Interrupt Translation Service (MSIs)",
    "arm,gic-v2m-frame": "GICv2m MSI frame",
    "arm,armv8-timer": "ARMv8 architected timer (per-CPU)",
    "arm,armv7-timer": "ARMv7 architected timer (per-CPU)",
    "arm,psci-1.0": "PSCI firmware interface (CPU on/off, reset, poweroff)",
    "arm,psci-0.2": "PSCI 0.2 firmware interface",
    "arm,psci": "PSCI firmware interface",
    "virtio,mmio": "virtio-mmio transport (a virtio device slot)",
    "pci-host-ecam-generic": "generic PCIe host bridge (ECAM config space)",
    "qemu,fw-cfg-mmio": "QEMU fw_cfg (firmware configuration interface)",
    "cfi-flash": "CFI NOR flash",
    "ns16550a": "16550A-compatible UART",
    "ns16550": "16550-compatible UART",
    "sifive,plic-1.0.0": "SiFive PLIC (platform-level interrupt controller)",
    "riscv,plic0": "RISC-V PLIC (platform-level interrupt controller)",
    "riscv,clint0": "RISC-V CLINT (timer + software interrupts)",
    "sifive,clint0": "SiFive CLINT (timer + software interrupts)",
    "riscv,cpu-intc": "per-hart local interrupt controller",
    "sifive,test1": "SiFive test finisher (QEMU: write 0x5555 = poweroff, 0x7777 = reset)",
    "sifive,test0": "SiFive test finisher (QEMU: write 0x5555 = poweroff, 0x7777 = reset)",
    "arm,armv8-pmuv3": "ARMv8 performance monitor unit (per-CPU)",
    "riscv,pmu": "RISC-V performance monitor (SBI PMU)",
    "qemu,platform": "QEMU dynamic sysbus (platform-bus) for -device hot-plug",
    "google,goldfish-rtc": "Goldfish real-time clock",
    "syscon": "system controller register block",
    "syscon-poweroff": "poweroff via a syscon register write",
    "syscon-reboot": "reboot via a syscon register write",
    "gpio-keys": "GPIO-connected buttons",
    "fixed-clock": "fixed-rate clock",
    "riscv,aplic": "RISC-V APLIC interrupt controller",
    "riscv,imsics": "RISC-V IMSIC (MSI controller)",
    "riscv": "RISC-V hart",
    "arm,cortex-a53": "Cortex-A53 CPU",
    "arm,cortex-a57": "Cortex-A57 CPU",
    "arm,cortex-a72": "Cortex-A72 CPU",
    "arm,arm-v8": "ARMv8 CPU",
}


def describe(node: Node) -> str:
    for c in compatibles(node):
        if c in KNOWN_DEVICES:
            return KNOWN_DEVICES[c]
    dt = strings(node.props.get("device_type", b""))
    if dt:
        return f"{dt[0]} node"
    return ""


def human_size(n: int) -> str:
    for unit, div in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= div and n % div == 0:
            return f"{n // div} {unit}"
    return f"{n:#x} bytes"
