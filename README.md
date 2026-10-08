# devicetree-mcp

**Let an AI agent read a devicetree the way a board bring-up engineer does.**

<!-- mcp-name: io.github.0xmortuex/devicetree-mcp -->

devicetree-mcp is an [MCP](https://modelcontextprotocol.io) server that loads devicetrees
(`.dtb`, or `.dts` when `dtc` is installed) and *explains* them. Embedded Linux, Zephyr,
U-Boot and hobby OSes all use devicetrees. The tool translates `reg` through every bus's
`ranges` to real CPU addresses, resolves interrupts to their controller and decodes the
specifier, follows phandles, decodes PCI interrupt swizzling, and finds the mistakes that
break bring-up.

It can also ask QEMU for the exact DTB its `-M virt` machine hands a guest. That's the
tree your kernel actually has to parse.

## What it looks like

Real output on QEMU 11's aarch64 and riscv64 `virt` machines:

```
> dt_load a qemu_arch=aarch64 qemu_args="-smp 2 -cpu cortex-a53 -m 1G"
Loaded 'a' from qemu-system-aarch64 -M virt -smp 2 -cpu cortex-a53 -m 1G: FDT v17, 59 nodes, 6 phandles, 0 labels.
2 CPU(s); memory: 0x40000000 (1 GiB)
dt_validate: 2 error(s), 1 warning(s).

> dt_node a serial0
pl011@9000000: PL011 UART, at 0x9000000 (4 KiB), IRQ SPI 1 (INTID 33) level-high on /intc@8000000 (GICv2 interrupt controller)
path: /pl011@9000000
aliases: serial0
reg encoding (from parent /): #address-cells=2 #size-cells=2
interrupt-parent (inherited): /intc@8000000
properties:
  clock-names = "uartclk", "apb_pclk"
  clocks = /apb-pclk (24 MHz); /apb-pclk (24 MHz)
  interrupts = SPI 1 (INTID 33) level-high on /intc@8000000
  reg = 0x9000000 size 0x1000 (4 KiB)

> dt_interrupt_map a        (excerpt)
/pcie@10000000 interrupt-map (16 entries, mask 0x1800 0x0 0x0 0x7):
  dev 00:00.0 INTA       -> SPI 3 (INTID 35) level-high on /intc@8000000
  dev 00:00.0 INTB       -> SPI 4 (INTID 36) level-high on /intc@8000000
  dev 00:01.0 INTA       -> SPI 4 (INTID 36) level-high on /intc@8000000
  ...

> dt_validate a
error: /cpus/cpu@0: next-level-cache = <0x0>: phandle values 0 and 0xffffffff are reserved by the spec and never name a node
error: /cpus/cpu@1: next-level-cache = <0x0>: ...
warning: /pcie@10000000: unit address @10000000 does not match the first reg address 0x4010000000 - rename the node to pcie@4010000000

> dt_memory_map r           (riscv64, excerpt)
  0x000000100000-0x000000100fff     4 KiB  /soc/test@100000  SiFive test finisher (QEMU: write 0x5555 = poweroff, 0x7777 = reset)
  0x000002000000-0x00000200ffff    64 KiB  /soc/clint@2000000  SiFive CLINT (timer + software interrupts)
  0x00000c000000-0x00000c5fffff     6 MiB  /soc/plic@c000000  SiFive PLIC (platform-level interrupt controller)
  0x000010000000-0x0000100000ff 0x100 bytes  /soc/serial@10000000  16550A-compatible UART
```

The findings above are real. QEMU 11's aarch64 `virt` really does emit
`next-level-cache = <0>` on each CPU, and its PCIe node is named after the low ECAM
address while `reg` holds the high one.

## Install

```bash
pip install devicetree-mcp
claude mcp add devicetree -- devicetree-mcp
```

Python 3.10+. Nothing else is required to read `.dtb` files: the parser is pure Python,
written from the Devicetree Specification. Optional extras:

- **dtc**, for `.dts` source: `apt install device-tree-compiler`, `brew install dtc`, or
  `pacman -S dtc`. Trees are compiled with `-@` so `&labels` work.
- **QEMU**, for `qemu_arch=`: the DTB comes from `-machine dumpdtb=`.

## Tools

| Tool | What it does |
|------|--------------|
| `dt_load` | Load a `.dtb`/`.dts` file, or `qemu_arch=` (+ `qemu_machine`, `qemu_args`) for QEMU's generated tree. Returns a summary: CPUs, memory, and how many problems `dt_validate` sees. |
| `dt_tree` | The hierarchy, one line per node: what it is, its CPU addresses, and its decoded interrupts. Runs of identical devices (e.g. 32 virtio slots) collapse to one line. |
| `dt_node` | One node with every property decoded: `reg` translated to CPU addresses, interrupts resolved, phandles to paths, clocks with frequencies, string lists, labels and aliases, and *who references it*. |
| `dt_find` | Nodes by compatible (substring), label, phandle, property, or name. |
| `dt_memory_map` | Every `reg` region translated through `ranges`, sorted, with overlaps flagged, `/memreserve/` entries, and anything not CPU-addressable. |
| `dt_interrupt_map` | Each interrupt controller, what feeds what, every device interrupt decoded (GIC SPI/PPI with INTID, PLIC sources, RISC-V hart-local causes), and `interrupt-map` tables. PCI maps show `bus:dev.fn` and INTA–D. |
| `dt_validate` | Structural checks: `reg`/`ranges` vs `#address-cells`/`#size-cells`, unit address vs `reg`, dangling or reserved phandles, interrupts without a routable parent or with the wrong cell count, duplicate unit addresses or phandles, overlapping regions. |
| `dt_diff` | Compare two trees: nodes added and removed, properties changed. Values are decoded and phandle renumbering is ignored. |
| `dt_trees` | List loaded trees. |

Nodes can be named by `/path` (unit address optional when unambiguous), `&label`, an
alias such as `serial0`, or `phandle:0x8002`.

## Limitations

- `dt_validate` checks structure, not bindings. It won't tell you a `pl011` node lacks
  `clock-names`. Use `dt-validate` from [dt-schema](https://github.com/devicetree-org/dt-schema)
  for that (on the [backlog](BACKLOG.md) as an optional integration).
- Interrupt specifiers are decoded for GICv2/v3, the RISC-V PLIC and hart-local
  controllers, and generic `<line flags>` controllers. Other controllers show raw cells.
- `.dts` files with `#include` need preprocessing with `cpp` first, as the Linux build
  does.
- There are no source line numbers yet. A DTB doesn't carry them, and dtc's annotation
  output is on the backlog.

## Tests

```bash
pip install ".[test]" "ruff==0.16.0" "mypy==2.3.0"
ruff check src tests && mypy --strict src
pytest tests
```

The unit tests run on real QEMU-generated DTBs (aarch64 and riscv64 `virt`, committed in
`tests/fixtures`) and on synthetic trees built with the package's own FDT encoder, so
every test also round-trips the encoder and parser. `tests/test_live.py` drives a real
QEMU (`dumpdtb`, including `gic-version=3` and `-smp` changes) and a real `dtc` (a `.dts`
with labels and a planted bug). CI installs both and fails if any live test skips.

## License

MIT
