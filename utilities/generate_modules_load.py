#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

"""Generate a sorted list of the kernel modules (`modules.load`) of the
options that some config fragments explicitly set, when they are `=m`.

Only the modules of these options are listed, not the ones they depend on or
select. Nothing is built.

The kernel configuration is built like a device does it: the BASE configs
are merged in order, then the TARGET configs (usually android-base.config,
its conditional XML and common.config) on top of them. Then:

  * the options of the TARGET configs that can be a module are set to `=m`
    and the configuration is resolved again;
  * the modules of the options that are still `=m` are found in the Makefiles
    of the kernel source (`obj-$(CONFIG_FOO) += foo.o`).

The modules are added to the output file if it already exists, so that the
list for all kernels and architectures can be collected by running the script
for each of them.

Example (a Debian arm64 kernel):

  generate_modules_load.py \\
      --kernel-source /path/to/kernel/source \\
      --arch arm64 \\
      --base /path/to/debian.config \\
      --base kernel/mainline/configs/fragments/android-base-pre/common.config \\
      --base kernel/mainline/configs/fragments/android-base-pre/arm64.config \\
      --target kernel/configs/b/android-6.12/android-base.config \\
      --target kernel/configs/b/android-6.12/android-base-conditional.xml \\
      --target kernel/mainline/configs/fragments/common.config \\
      --output kernel/mainline/configs/modprobe/modules.load
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

OPTION_RE = re.compile(r'^(?:# )?(CONFIG_\w+)(?:=(.*)| is not set)$')
OBJ_RE = re.compile(r'^obj-\$\((CONFIG_\w+)\)\s*[:+]?=\s*(.*)$')

# Directory of arch/ for ARCH, when it is not the same
SRCARCH = {'x86_64': 'x86', 'i386': 'x86'}


def read_config(path):
    """Read a config file as {option: value}, with None for "is not set"."""
    config = {}
    with open(path) as f:
        for line in f:
            m = OPTION_RE.match(line.strip())
            if m:
                config[m.group(1)] = None if m.group(2) == 'n' else m.group(2)
    return config


def write_config(path, config):
    with open(path, 'w') as f:
        for key, value in config.items():
            f.write(f'# {key} is not set\n' if value is None
                    else f'{key}={value}\n')


def xml_to_config(path, conditions_config, out_path):
    """Convert an android-base-conditional.xml to a config file, keeping only
    the groups whose conditions are met by `conditions_config`."""
    def entries(parent):
        for c in parent.findall('config'):
            value = c.find('value')
            text = (value.text or '').strip()
            yield c.find('key').text.strip(), (
                f'"{text}"' if value.get('type') == 'string' else text)

    def met(key, value):
        return conditions_config.get(key) == (None if value == 'n' else value)

    config = {}
    # The file has several top level elements: give it a root
    with open(path) as f:
        root = ET.fromstring('<root>' + f.read() + '</root>')
    for group in root.findall('group'):
        conditions = group.find('conditions')
        if conditions is None or all(met(k, v) for k, v in entries(conditions)):
            for key, value in entries(group):
                config[key] = None if value == 'n' else value
    write_config(out_path, config)


def modules_of_options(source, arch, options):
    """Modules built for the options, as `obj-$(CONFIG_FOO) += foo.o`.

    Directories (`obj-$(CONFIG_FOO) += foo/`) and objects added to a module
    (`foo-$(CONFIG_BAR) += bar.o`) are not modules of the option.
    """
    other_archs = {d for d in os.listdir(os.path.join(source, 'arch'))
                   if d != SRCARCH.get(arch, arch)}
    modules = set()
    for root, dirs, files in os.walk(source):
        if root == os.path.join(source, 'arch'):
            dirs[:] = [d for d in dirs if d not in other_archs]
        dirs[:] = [d for d in dirs if d not in ('.git', 'Documentation', 'tools')]
        for name in ('Makefile', 'Kbuild'):
            if name not in files:
                continue
            with open(os.path.join(root, name), errors='replace') as f:
                text = f.read().replace('\\\n', ' ')
            for line in text.splitlines():
                m = OBJ_RE.match(line.split('#')[0].strip())
                if m and m.group(1) in options:
                    modules.update(os.path.basename(obj)[:-2] + '.ko'
                                   for obj in m.group(2).split()
                                   if obj.endswith('.o') and '$' not in obj)
    return modules


class Kernel:
    def __init__(self, source, arch, workdir, make_args):
        self.source = source
        self.arch = arch
        self.workdir = workdir
        self.make_args = ['make', '-C', source, f'ARCH={arch}'] + make_args

    def make(self, out, target):
        subprocess.run(self.make_args + [f'O={out}', target], check=True,
                       stdout=sys.stderr)

    def outdir(self, name):
        out = os.path.join(self.workdir, name)
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        return out

    def merge(self, name, files):
        """Merge config files like the device build does, and resolve them."""
        out = self.outdir(name)
        subprocess.run([os.path.join(self.source, 'scripts/kconfig/merge_config.sh'),
                        '-m', '-O', out] + files,
                       env=dict(os.environ, ARCH=self.arch), check=True,
                       stdout=sys.stderr)
        self.make(out, 'olddefconfig')
        return out

    def resolve(self, name, config):
        out = self.outdir(name)
        write_config(os.path.join(out, '.config'), config)
        self.make(out, 'olddefconfig')
        return read_config(os.path.join(out, '.config'))

    def tristate_options(self, out, options):
        """The enabled options that can be a module: `yes2modconfig` changes
        the `y` of exactly such options to `m`."""
        config = read_config(os.path.join(out, '.config'))
        self.make(out, 'yes2modconfig')
        as_modules = read_config(os.path.join(out, '.config'))
        return [o for o in options
                if config.get(o) in ('y', 'm') and as_modules.get(o) == 'm']


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kernel-source', required=True)
    parser.add_argument('--arch', default='arm64')
    parser.add_argument('--base', action='append', default=[],
                        help='config merged first (repeatable, in order)')
    parser.add_argument('--target', action='append', required=True,
                        help='config whose options are set to =m (repeatable); '
                             'an android-base-conditional.xml is accepted too')
    parser.add_argument('--output', required=True)
    parser.add_argument('--workdir',
                        default=os.path.join(tempfile.gettempdir(), 'modules-load'),
                        help='directory for temporary files (default: %(default)s)')
    parser.add_argument('--make-arg', action='append', default=['LLVM=1'],
                        help='extra make argument (default: LLVM=1)')
    args = parser.parse_args()

    kernel = Kernel(os.path.abspath(args.kernel_source), args.arch,
                    os.path.abspath(args.workdir), args.make_arg)
    os.makedirs(kernel.workdir, exist_ok=True)
    base = [os.path.abspath(f) for f in args.base]
    target = [os.path.abspath(f) for f in args.target]

    # The conditions of the XMLs are checked against the configuration
    # without the targets
    if any(f.endswith('.xml') for f in target):
        base_config = read_config(os.path.join(kernel.merge('base', base), '.config'))
        for i, f in enumerate(target):
            if f.endswith('.xml'):
                converted = os.path.join(kernel.workdir, f'conditional-{i}.config')
                xml_to_config(f, base_config, converted)
                target[i] = converted

    merged = kernel.merge('merged', base + target)
    config = read_config(os.path.join(merged, '.config'))
    options = {}
    for path in target:
        options.update(read_config(path))
    tristate = kernel.tristate_options(merged, options)
    print(f'{len(options)} options in the target configs, '
          f'{len(tristate)} can be modules', file=sys.stderr)

    # Keep the ones that are still modules once the configuration is resolved
    resolved = kernel.resolve('modules', dict(config, **{k: 'm' for k in tristate}))
    enabled = {o for o in tristate if resolved.get(o) == 'm'}
    modules = modules_of_options(kernel.source, args.arch, enabled)

    existing = set()
    if os.path.exists(args.output):
        with open(args.output) as f:
            existing = {line.strip() for line in f if line.strip()}
    with open(args.output, 'w') as f:
        f.write(''.join(f'{m}\n' for m in sorted(existing | modules)))
    print(f'{len(modules - existing)} modules added to {args.output}, '
          f'{len(existing)} already there', file=sys.stderr)


if __name__ == '__main__':
    main()
