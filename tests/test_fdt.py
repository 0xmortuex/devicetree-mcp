import struct

import pytest

from builders import BOARD, cells, make, qemu_fixture, tree
from devicetree_mcp.fdt import FDT_END, FDT_MAGIC, FDTError, build, parse


def test_roundtrip_preserves_tree_and_reservations():
    t = tree(BOARD, reservations=[(0x80000000, 0x100000)])
    assert t.fdt.reservations == [(0x80000000, 0x100000)]
    assert t.node("/bus@40000000/timer@2000").props["reg"] == cells(0x2000, 0x100)
    assert [c.name for c in t.fdt.root.children][:3] == ["memory@80000000", "intc@8000000", "clk24"]


def test_real_qemu_dtbs_parse():
    a = qemu_fixture("aarch64")
    r = qemu_fixture("riscv64")
    assert a.fdt.version == 17 and len(a.by_path) == 59
    assert "/pl011@9000000" in a.by_path
    assert "/soc/plic@c000000" in r.by_path


def test_bad_magic_mentions_dts():
    with pytest.raises(FDTError, match="not a .dtb.*dtc"):
        parse(b"/dts-v1/;\n/ { };\n" + b"\0" * 40)


def test_truncated_blob():
    data = build(make(BOARD))
    with pytest.raises(FDTError, match="truncated"):
        parse(data[:-10])


def test_too_small():
    with pytest.raises(FDTError, match="too small"):
        parse(b"\xd0\x0d\xfe\xed")


def test_unknown_token_is_reported_with_offset():
    data = bytearray(build(make({"props": {}})))
    off_struct = struct.unpack_from(">I", data, 8)[0]
    # Root BEGIN_NODE (4) + empty name padded to 4 -> next token at +8.
    struct.pack_into(">I", data, off_struct + 8, 0x7)
    with pytest.raises(FDTError, match="unknown structure token 0x7"):
        parse(bytes(data))


def test_nop_tokens_are_skipped():
    root = make({"props": {"x": cells(1)}})
    data = bytearray(build(root))
    off_struct, off_strings = struct.unpack_from(">II", data, 8)
    # Replace FDT_END with NOP + END by appending: rebuild header sizes.
    body = bytes(data[off_struct:off_strings])
    assert body.endswith(struct.pack(">I", FDT_END))
    body = body[:-4] + struct.pack(">II", 0x4, FDT_END)
    strings = bytes(data[off_strings:])
    total = off_struct + len(body) + len(strings)
    hdr = struct.pack(">10I", FDT_MAGIC, total, off_struct, off_struct + len(body), 40, 17, 16, 0,
                      len(strings), len(body))
    blob = hdr + bytes(data[40:off_struct]) + body + strings
    assert parse(blob).root.props["x"] == cells(1)
