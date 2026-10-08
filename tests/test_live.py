"""Against real tools when present: QEMU (-machine dumpdtb) and dtc.

Skipped when the tool is missing; CI installs both so these always run there.
"""

import os
import shutil

import pytest

from devicetree_mcp import loader
from devicetree_mcp import server as S

HERE = os.path.dirname(__file__)


def _have_qemu(arch):
    try:
        loader.find_qemu(arch)
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def fresh():
    S._trees.clear()
    yield
    S._trees.clear()


@pytest.mark.skipif(not _have_qemu("aarch64"), reason="qemu-system-aarch64 not installed")
def test_qemu_aarch64_cpu_count_follows_smp_and_diff_shows_it():
    assert "2 CPU(s)" in S.dt_load("two", qemu_arch="aarch64", qemu_args="-smp 2 -cpu cortex-a53")
    assert "4 CPU(s)" in S.dt_load("four", qemu_arch="aarch64", qemu_args="-smp 4 -cpu cortex-a53")
    diff = S.dt_diff("two", "four")
    assert "+ /cpus/cpu@2" in diff and "+ /cpus/cpu@3" in diff
    assert "pl011@9000000: PL011 UART" in S.dt_tree("two", depth=1)


@pytest.mark.skipif(not _have_qemu("aarch64"), reason="qemu-system-aarch64 not installed")
def test_qemu_aarch64_gicv3_is_explained():
    S.dt_load("g3", qemu_arch="aarch64", qemu_machine="virt,gic-version=3")
    out = S.dt_interrupt_map("g3")
    assert "(GICv3 interrupt controller)" in out
    assert "SPI 1 (INTID 33) level-high" in out


@pytest.mark.skipif(not _have_qemu("riscv64"), reason="qemu-system-riscv64 not installed")
def test_qemu_riscv64_validates_clean_and_maps_memory():
    out = S.dt_load("rv", qemu_arch="riscv64", qemu_args="-smp 2 -m 512")
    assert "memory: 0x80000000 (512 MiB)" in out
    assert S.dt_validate("rv") == "No structural problems found."


def test_qemu_bad_arch_is_explained():
    with pytest.raises(Exception, match="qemu-system-nosucharch not found"):
        S.dt_load("x", qemu_arch="nosucharch")


@pytest.mark.skipif(shutil.which("dtc") is None, reason="dtc not installed")
def test_dts_source_with_labels_and_a_planted_bug():
    out = S.dt_load("b", path=os.path.join(HERE, "fixtures", "board.dts"))
    assert "Loaded 'b'" in out
    # dtc -@ keeps labels, so &label works.
    assert "path: /soc/serial@1000" in S.dt_node("b", "&uart0")
    # board.dts deliberately names a node @2000 while its reg says 0x3000.
    assert "rename the node to timer@3000" in S.dt_validate("b")
    # The soc bus translates 0x1000 to CPU 0x40001000.
    assert "0x000040001000" in S.dt_memory_map("b")


@pytest.mark.skipif(shutil.which("dtc") is None, reason="dtc not installed")
def test_dtc_errors_are_passed_through(tmp_path):
    bad = tmp_path / "bad.dts"
    bad.write_text("/dts-v1/;\n/ { foo = <1 ; };\n", encoding="utf-8")
    with pytest.raises(Exception, match="dtc failed to compile"):
        S.dt_load("bad", path=str(bad))
