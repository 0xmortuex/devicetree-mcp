"""Flattened devicetree (DTB) parser, written to the Devicetree Specification v0.4 §5.

A DTB is four parts:

    header (40 bytes, big-endian u32s)
    memory reservation block   (u64 address, u64 size) pairs, ended by 0,0
    structure block            tokens: BEGIN_NODE name / PROP len nameoff value / END_NODE / NOP / END
    strings block              NUL-terminated property names, referenced by offset

Everything is big-endian and 4-byte aligned. This module only turns bytes into a
tree; meaning (cells, phandles, interrupts) lives in `model.py`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE = 0x1
FDT_END_NODE = 0x2
FDT_PROP = 0x3
FDT_NOP = 0x4
FDT_END = 0x9


class FDTError(ValueError):
    pass


@dataclass
class Node:
    name: str                      # "uart@9000000", "" for the root
    path: str                      # "/pl011@9000000"
    parent: Node | None
    props: dict[str, bytes] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)

    @property
    def base_name(self) -> str:
        return self.name.split("@", 1)[0]

    @property
    def unit_address(self) -> str | None:
        return self.name.split("@", 1)[1] if "@" in self.name else None

    def child(self, name: str) -> Node | None:
        for c in self.children:
            if c.name == name:
                return c
        # Allow omitting the unit address when it's unambiguous ("memory").
        matches = [c for c in self.children if c.base_name == name]
        return matches[0] if len(matches) == 1 else None

    def walk(self) -> list[Node]:
        out = [self]
        for c in self.children:
            out.extend(c.walk())
        return out


@dataclass
class FDT:
    version: int
    last_comp_version: int
    boot_cpuid: int
    reservations: list[tuple[int, int]]
    root: Node
    size: int                       # totalsize from the header

    def nodes(self) -> list[Node]:
        return self.root.walk()


def _cstr(data: bytes, off: int, limit: int) -> tuple[str, int]:
    end = data.find(b"\0", off, limit)
    if end < 0:
        raise FDTError(f"unterminated string at offset {off:#x}")
    return data[off:end].decode("utf-8", errors="replace"), end + 1


def _align4(n: int) -> int:
    return (n + 3) & ~3


def parse(data: bytes) -> FDT:
    if len(data) < 40:
        raise FDTError(f"file is {len(data)} bytes - too small for an FDT header (40)")
    (magic, totalsize, off_struct, off_strings, off_rsvmap, version,
     last_comp, boot_cpuid, size_strings, size_struct) = struct.unpack_from(">10I", data, 0)
    if magic != FDT_MAGIC:
        raise FDTError(
            f"bad magic {magic:#010x} (expected 0xd00dfeed) - this is not a .dtb. "
            f"A .dts source must be compiled first (dt_load does this when dtc is installed)."
        )
    if totalsize > len(data):
        raise FDTError(f"header says {totalsize} bytes but the file has {len(data)} - truncated")
    if version < 16:
        raise FDTError(f"FDT version {version} is too old; versions 16-17 are supported")
    if version < 17:
        size_struct = totalsize - off_struct  # v16 headers have no size_dt_struct
    for name, off, size in (("structure", off_struct, size_struct),
                            ("strings", off_strings, size_strings)):
        if off + size > totalsize:
            raise FDTError(f"{name} block ({off:#x}+{size:#x}) runs past the end of the blob")

    reservations: list[tuple[int, int]] = []
    pos = off_rsvmap
    while True:
        if pos + 16 > totalsize:
            raise FDTError("memory reservation map is not terminated")
        addr, size = struct.unpack_from(">QQ", data, pos)
        pos += 16
        if addr == 0 and size == 0:
            break
        reservations.append((addr, size))

    strings_end = off_strings + size_strings
    struct_end = off_struct + size_struct
    pos = off_struct
    root: Node | None = None
    stack: list[Node] = []
    while True:
        if pos + 4 > struct_end:
            raise FDTError("structure block ended without FDT_END")
        (token,) = struct.unpack_from(">I", data, pos)
        pos += 4
        if token == FDT_BEGIN_NODE:
            name, pos = _cstr(data, pos, struct_end)
            pos = _align4(pos)
            parent = stack[-1] if stack else None
            if parent is None:
                if root is not None:
                    raise FDTError("more than one root node")
                path = "/"
            else:
                path = (parent.path.rstrip("/") + "/" + name)
            node = Node(name, path, parent)
            if parent is not None:
                parent.children.append(node)
            else:
                root = node
            stack.append(node)
        elif token == FDT_END_NODE:
            if not stack:
                raise FDTError(f"unbalanced END_NODE at offset {pos - 4:#x}")
            stack.pop()
        elif token == FDT_PROP:
            if not stack:
                raise FDTError(f"property outside any node at offset {pos - 4:#x}")
            length, nameoff = struct.unpack_from(">II", data, pos)
            pos += 8
            if pos + length > struct_end:
                raise FDTError(f"property value runs past the structure block at {pos:#x}")
            pname, _ = _cstr(data, off_strings + nameoff, strings_end)
            stack[-1].props[pname] = bytes(data[pos:pos + length])
            pos = _align4(pos + length)
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            break
        else:
            raise FDTError(f"unknown structure token {token:#x} at offset {pos - 4:#x}")
    if stack:
        raise FDTError(f"{len(stack)} node(s) never closed (missing END_NODE)")
    if root is None:
        raise FDTError("structure block has no root node")
    return FDT(version, last_comp, boot_cpuid, reservations, root, totalsize)


# -- encoder (used by tests and dt_diff fixtures; also handy for users) ---------

def build(root: Node, reservations: list[tuple[int, int]] | None = None,
          boot_cpuid: int = 0) -> bytes:
    """Serialize a Node tree back to a version-17 DTB."""
    strings = bytearray()
    str_off: dict[str, int] = {}
    body = bytearray()

    def name_off(n: str) -> int:
        if n not in str_off:
            str_off[n] = len(strings)
            strings.extend(n.encode() + b"\0")
        return str_off[n]

    def emit(node: Node) -> None:
        body.extend(struct.pack(">I", FDT_BEGIN_NODE))
        nm = node.name.encode() + b"\0"
        body.extend(nm + b"\0" * (_align4(len(nm)) - len(nm)))
        for k, v in node.props.items():
            body.extend(struct.pack(">III", FDT_PROP, len(v), name_off(k)))
            body.extend(v + b"\0" * (_align4(len(v)) - len(v)))
        for c in node.children:
            emit(c)
        body.extend(struct.pack(">I", FDT_END_NODE))

    emit(root)
    body.extend(struct.pack(">I", FDT_END))
    rsv = b"".join(struct.pack(">QQ", a, s) for a, s in (reservations or [])) + b"\0" * 16
    off_rsv = 40
    off_struct = off_rsv + len(rsv)
    off_strings = off_struct + len(body)
    total = off_strings + len(strings)
    header = struct.pack(">10I", FDT_MAGIC, total, off_struct, off_strings, off_rsv, 17, 16,
                         boot_cpuid, len(strings), len(body))
    return header + rsv + bytes(body) + bytes(strings)
