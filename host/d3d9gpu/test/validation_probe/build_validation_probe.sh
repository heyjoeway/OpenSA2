#!/bin/bash
# Build the child checkpoint layer against the existing pinned DXVK headers.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=${SA2_REPO:-$(cd "$HERE/../../../.." && pwd -P)}
[ $# -eq 1 ] || { echo "usage: build_validation_probe.sh OUT" >&2; exit 2; }
[ ! -e "$1" ] || { echo "observer build requires a fresh directory" >&2; exit 2; }
mkdir -p "$1"
OUT=$(cd "$1" && pwd -P)
VKINC=$REPO/private/s5-tools/src/dxvk-3.1.1/include/vulkan/include
python3 - "$HERE" "$OUT" "$REPO" "${CC:-gcc}" "$VKINC" <<'PY_START'
from pathlib import Path
import hashlib, json, shutil, sys
source, output, root = (Path(v) for v in sys.argv[1:4])
compiler = Path(shutil.which(sys.argv[4])).resolve(strict=True)
headers = Path(sys.argv[5]); sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
record = {'compiler':{'path':str(compiler),'sha256':sha(compiler)},
    'sources':{str(p.relative_to(root)):sha(p) for p in [source/'validation_probe.c',source/'validation_probe.json.in',source/'build_validation_probe.sh']},
    'headers':{str(p.relative_to(root)):sha(p) for p in sorted(headers.rglob('*')) if p.is_file()}}
(output/'validation_probe_start.json').write_text(json.dumps(record,indent=2)+'\n')
PY_START
${CC:-gcc} -std=c11 -O2 -Wall -Wextra -Werror -fPIC -shared -fvisibility=hidden -D_GNU_SOURCE \
  -I"$VKINC" -o "$OUT/libVkLayer_sa2_validation_probe.so" "$HERE/validation_probe.c" -lpthread -ldl
python3 - "$HERE" "$OUT" "$REPO" "${CC:-gcc}" "$VKINC" <<'PY'
from pathlib import Path
import hashlib, json, shutil, sys
source, output, root = (Path(v) for v in sys.argv[1:4])
compiler = Path(shutil.which(sys.argv[4])).resolve(strict=True)
headers = Path(sys.argv[5])
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
manifest = json.loads((source / 'validation_probe.json.in').read_text())
manifest['layer']['library_path'] = str(output / 'libVkLayer_sa2_validation_probe.so')
(output / 'validation_probe.json').write_text(json.dumps(manifest, indent=2) + '\n')
record = {'schema':'sa2-validation-probe-build/v1',
    'compiler':{'path':str(compiler),'sha256':sha(compiler)},
    'sources':{str(p.relative_to(root)):sha(p) for p in
        [source/'validation_probe.c',source/'validation_probe.json.in',source/'build_validation_probe.sh']},
    'headers':{str(p.relative_to(root)):sha(p) for p in sorted(headers.rglob('*')) if p.is_file()},
    'outputs':{p.name:sha(p) for p in [output/'validation_probe.json',output/'libVkLayer_sa2_validation_probe.so']}}
start=json.loads((output/'validation_probe_start.json').read_text())
if {k:record[k] for k in start} != start: raise SystemExit('observer inputs changed during build')
(output / 'validation_probe_build.json').write_text(json.dumps(record, indent=2)+'\n')
PY
echo "VALIDATION_PROBE_BUILD_OK $OUT/validation_probe.json"
