# Kernel configuration for mainline Android

Kernel config fragments and defconfigs for running Android on mainline
(or close to mainline) Linux kernels. They are consumed through
`TARGET_KERNEL_CONFIG` and `TARGET_KERNEL_CONFIG_EXT` of the LineageOS
kernel build rules (`vendor/lineage/build/tasks/kernel.mk`).

## Layout

| Path | Contents |
|------|----------|
| `defconfigs/<arch>/gki_defconfig` | A GKI-like defconfig, for kernels that do not ship one |
| `fragments/android-base-pre/` | Android requirements that the kernel does not meet by default, with the dependencies they need (see its README) |
| `fragments/android-base-conditional/` | The conditional requirements of Android (`CONFIG_ARM64=y`, `CONFIG_X86_64=y`, ...) |
| `fragments/common.config` | Options commonly missing or mismatching in every kernel configuration |
| `fragments/y/` | Optional features to enable (`fbcon.config`, `compressed-modules.config`, `compressed-firmware.config`, `arm64/pagesize-16k.config`) |
| `fragments/n/` | Optional features to disable (faster builds, Go devices, no Rust, ...) |
| `utilities/validate_kernel_config.py` | Checks a `.config` against a fragment |
| `utilities/generate_modules_load.py` | Lists the modules of the options of fragments when they are `=m`, without their dependencies (see `modprobe/modules.load`; no kernel build needed) |
| `modprobe/modules.load` | The modules to load for the options that Android needs, whatever the kernel and architecture; collected with the script above |
| `init/` | init scripts for the kernel features above |

The Android requirements themselves are not copied here. They come from
`kernel/configs/<letter>/android-<version>/android-base.config`.

## Building a kernel configuration

Order matters: a later fragment overrides an earlier one. A kernel
configuration is usually made of, in this order:

1. The base: an arch defconfig, `gki_defconfig`, or distribution config
   files such as the ones of Debian.
2. Device or distribution specific fixes.
3. `fragments/android-base-pre/common.config` and the one for the
   architecture (`arm64.config`, `x86_64.config`).
4. `android-base.config` from `kernel/configs`.
5. The `android-base-conditional/` fragments that apply
   (`CONFIG_ARM64-y.config`, `CONFIG_X86-y.config`, ...).
6. `fragments/common.config`.
7. Optional `y/` and `n/` fragments, then device customizations.

`android-base-pre/` is written against the arch defconfig
(`arch/arm64/configs/defconfig`, `arch/x86/configs/x86_64_defconfig`).
With another base, some of its options may be unneeded, and others
may be needed.

Examples: `device/mainline/generic/Generic_*/configs/kernel/debian.mk`
(Debian config files as the base),
`device/mainline/generic/Generic_arm64/kernels/asahi/board.mk` and
`device/pc/basic_x86_64_pc/BoardConfig.mk` (arch defconfig as the base).

### Pitfalls

- `kernel.mk` only merges the files in `TARGET_KERNEL_CONFIG_EXT` that
  are named `*.config`. A file named otherwise, such as Debian's
  `config`, is silently skipped (unless it is the first one, which
  becomes the base). Copy or concatenate it to a `*.config` file.
- By default every fragment is merged and resolved with `olddefconfig`
  one at a time, and an option whose dependencies are enabled by a
  later fragment is dropped. When the base does not enable the
  dependencies (e.g. a distribution config without a defconfig), set
  `MERGE_ALL_KERNEL_CONFIGS_AT_ONCE := true` to merge all of them
  before resolving.
- `CONFIG_X=m` in a base (`HID`, `INET_DIAG`, ...) can keep options that
  Android wants built-in as modules. Set the dependency to `y` in a
  fragment, or load the module from `modules.load`.

## Validating

Generate the `.config` of the target, then compare it with the Android
requirements:

```
cat kernel/configs/b/android-6.12/android-base.config \
    kernel/mainline/configs/fragments/android-base-conditional/CONFIG_ARM64-y.config > android.frag
python3 kernel/mainline/configs/utilities/validate_kernel_config.py \
    <kernel out>/.config android.frag
```

The output lists the matching, mismatching and missing entries. Only a
few are expected to differ:

- Options that no longer exist or are deprecated:
  `ANDROID_LOW_MEMORY_KILLER`, `ANDROID_PARANOID_NETWORK`, `BPFILTER`,
  `USELIB`, `DEVKMEM`, `NF_CT_PROTO_DCCP`, `NF_CT_PROTO_UDPLITE`,
  `SCHED_DEBUG`, `ARM64_PAN`.
- `CFI_CLANG`, which needs a different kernel setup.
- `FW_CACHE`, which cannot be disabled in recent kernels.
- `STATIC_USERMODEHELPER`, which is intentionally unset in some
  configurations.
