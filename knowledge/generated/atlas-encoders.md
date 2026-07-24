# Atlas · encoders (57 modules)

**Học:** Biến đổi payload theo arch → entropy/heuristic
**Lab:** Signature only
**Role:** malware-static

## Depth-1 (đầy đủ, không cắt)

- `encoders/x86` ×24
- `encoders/cmd` ×8
- `encoders/riscv32le` ×4
- `encoders/riscv64le` ×4
- `encoders/x64` ×4
- `encoders/php` ×3
- `encoders/generic` ×2
- `encoders/mipsbe` ×2
- `encoders/mipsle` ×2
- `encoders/ppc` ×2
- `encoders/ruby` ×1
- `encoders/sparc` ×1

## Depth-2 (đầy đủ, không cắt)

- `encoders/cmd/base64.rb` ×1
- `encoders/cmd/brace.rb` ×1
- `encoders/cmd/echo.rb` ×1
- `encoders/cmd/generic_sh.rb` ×1
- `encoders/cmd/ifs.rb` ×1
- `encoders/cmd/perl.rb` ×1
- `encoders/cmd/powershell_base64.rb` ×1
- `encoders/cmd/printf_php_mq.rb` ×1
- `encoders/generic/eicar.rb` ×1
- `encoders/generic/none.rb` ×1
- `encoders/mipsbe/byte_xori.rb` ×1
- `encoders/mipsbe/longxor.rb` ×1
- `encoders/mipsle/byte_xori.rb` ×1
- `encoders/mipsle/longxor.rb` ×1
- `encoders/php/base64.rb` ×1
- `encoders/php/hex.rb` ×1
- `encoders/php/minify.rb` ×1
- `encoders/ppc/longxor.rb` ×1
- `encoders/ppc/longxor_tag.rb` ×1
- `encoders/riscv32le/byte_xori.rb` ×1
- `encoders/riscv32le/longxor.rb` ×1
- `encoders/riscv32le/longxor_feedback.rb` ×1
- `encoders/riscv32le/longxor_tag.rb` ×1
- `encoders/riscv64le/byte_xori.rb` ×1
- `encoders/riscv64le/longxor.rb` ×1
- `encoders/riscv64le/longxor_feedback.rb` ×1
- `encoders/riscv64le/longxor_tag.rb` ×1
- `encoders/ruby/base64.rb` ×1
- `encoders/sparc/longxor_tag.rb` ×1
- `encoders/x64/xor.rb` ×1
- `encoders/x64/xor_context.rb` ×1
- `encoders/x64/xor_dynamic.rb` ×1
- `encoders/x64/zutto_dekiru.rb` ×1
- `encoders/x86/add_sub.rb` ×1
- `encoders/x86/alpha_mixed.rb` ×1
- `encoders/x86/alpha_upper.rb` ×1
- `encoders/x86/avoid_underscore_tolower.rb` ×1
- `encoders/x86/avoid_utf8_tolower.rb` ×1
- `encoders/x86/bloxor.rb` ×1
- `encoders/x86/bmp_polyglot.rb` ×1
- `encoders/x86/call4_dword_xor.rb` ×1
- `encoders/x86/context_cpuid.rb` ×1
- `encoders/x86/context_stat.rb` ×1
- `encoders/x86/context_time.rb` ×1
- `encoders/x86/countdown.rb` ×1
- `encoders/x86/fnstenv_mov.rb` ×1
- `encoders/x86/jmp_call_additive.rb` ×1
- `encoders/x86/nonalpha.rb` ×1
- `encoders/x86/nonupper.rb` ×1
- `encoders/x86/opt_sub.rb` ×1
- `encoders/x86/service.rb` ×1
- `encoders/x86/shikata_ga_nai.rb` ×1
- `encoders/x86/single_static_bit.rb` ×1
- `encoders/x86/unicode_mixed.rb` ×1
- `encoders/x86/unicode_upper.rb` ×1
- `encoders/x86/xor_dynamic.rb` ×1
- `encoders/x86/xor_poly.rb` ×1

> Nguồn: `python3 scripts/metasploit_full_atlas.py`
> Cấm: exploit run · msfvenom · scan prod

