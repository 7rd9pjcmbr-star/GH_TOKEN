# Atlas · nops (14 modules)

**Học:** NOP sled patterns theo arch
**Lab:** Pattern heuristic
**Role:** malware-static

## Depth-1 (đầy đủ, không cắt)

- `nops/x86` ×2
- `nops/aarch64` ×1
- `nops/armle` ×1
- `nops/cmd` ×1
- `nops/loongarch64` ×1
- `nops/mipsbe` ×1
- `nops/php` ×1
- `nops/ppc` ×1
- `nops/riscv32le` ×1
- `nops/riscv64le` ×1
- `nops/sparc` ×1
- `nops/tty` ×1
- `nops/x64` ×1

## Depth-2 (đầy đủ, không cắt)

- `nops/aarch64/simple.rb` ×1
- `nops/armle/simple.rb` ×1
- `nops/cmd/generic.rb` ×1
- `nops/loongarch64/simple.rb` ×1
- `nops/mipsbe/better.rb` ×1
- `nops/php/generic.rb` ×1
- `nops/ppc/simple.rb` ×1
- `nops/riscv32le/simple.rb` ×1
- `nops/riscv64le/simple.rb` ×1
- `nops/sparc/random.rb` ×1
- `nops/tty/generic.rb` ×1
- `nops/x64/simple.rb` ×1
- `nops/x86/opty2.rb` ×1
- `nops/x86/single_byte.rb` ×1

> Nguồn: `python3 scripts/metasploit_full_atlas.py`
> Cấm: exploit run · msfvenom · scan prod

