"""Build small DTBs in tests without needing dtc: a tree of dicts -> FDT bytes."""

from __future__ import annotations

import gzip
import os
import struct
from typing import Any

from devicetree_mcp.fdt import Node, build, parse
from devicetree_mcp.model import Tree

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def cells(*vals: int) -> bytes:
    return struct.pack(f">{len(vals)}I", *vals)


def s(*vals: str) -> bytes:
    return b"".join(v.encode() + b"\0" for v in vals)


def make(spec: dict[str, Any], name: str = "", parent: Node | None = None) -> Node:
    """spec: {"props": {...}, "children": {"uart@1000": {...}}}"""
    path = "/" if parent is None else parent.path.rstrip("/") + "/" + name
    node = Node(name, path, parent, dict(spec.get("props", {})))
    for child_name, child_spec in spec.get("children", {}).items():
        node.children.append(make(child_spec, child_name, node))
    return node


def tree(spec: dict[str, Any], reservations: list[tuple[int, int]] | None = None) -> Tree:
    """Round-trip through the encoder AND parser, so every test also tests both."""
    return Tree(parse(build(make(spec), reservations)), "<test>")


def qemu_fixture(arch: str) -> Tree:
    if arch == "aarch64":
        with gzip.open(os.path.join(FIXTURES, "qemu-virt-aarch64.dtb.gz"), "rb") as fh:
            data = fh.read()
    else:
        with open(os.path.join(FIXTURES, "qemu-virt-riscv64.dtb"), "rb") as fh:
            data = fh.read()
    return Tree(parse(data), f"qemu-virt-{arch} fixture")


# A small but realistic board: GIC, a UART on the root, a nested bus with an
# address-translating `ranges`, a clock, labels, and an alias.
BOARD: dict[str, Any] = {
    "props": {
        "#address-cells": cells(1), "#size-cells": cells(1),
        "compatible": s("acme,board"), "model": s("ACME Test Board"),
        "interrupt-parent": cells(1),
    },
    "children": {
        "memory@80000000": {"props": {"device_type": s("memory"), "reg": cells(0x80000000, 0x10000000)}},
        "intc@8000000": {"props": {
            "compatible": s("arm,gic-400"), "interrupt-controller": b"",
            "#interrupt-cells": cells(3), "reg": cells(0x8000000, 0x1000, 0x8010000, 0x1000),
            "phandle": cells(1)}},
        "clk24": {"props": {"compatible": s("fixed-clock"), "#clock-cells": cells(0),
                            "clock-frequency": cells(24_000_000), "phandle": cells(2)}},
        "uart@9000000": {"props": {
            "compatible": s("arm,pl011", "arm,primecell"), "reg": cells(0x9000000, 0x1000),
            "interrupts": cells(0, 1, 4), "clocks": cells(2, 2), "clock-names": s("uartclk", "apb_pclk"),
            "phandle": cells(3)}},
        "bus@40000000": {"props": {
            "compatible": s("simple-bus"), "#address-cells": cells(1), "#size-cells": cells(1),
            "ranges": cells(0x0, 0x40000000, 0x100000)},
            "children": {
                "timer@2000": {"props": {"compatible": s("acme,timer"), "reg": cells(0x2000, 0x100),
                                         "interrupts": cells(0, 5, 1)}},
            }},
        "isolated": {"props": {"#address-cells": cells(1), "#size-cells": cells(1)},
                     "children": {"thing@10": {"props": {"reg": cells(0x10, 0x4)}}}},
        "aliases": {"props": {"serial0": s("/uart@9000000")}},
        "__symbols__": {"props": {"uart0": s("/uart@9000000"), "gic": s("/intc@8000000")}},
    },
}
