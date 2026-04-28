#!/bin/bash
# Download 3D Slicer's volume-rendering presets for medical CT/MR.
#
# These ~25 presets define color + opacity transfer functions tuned for
# specific clinical scenarios (CT-Bone, CT-Lung, CT-AAA, MR-Default, ...).
# Source: https://github.com/Slicer/Slicer
# License: BSD-style (see Slicer-LICENSE.txt after download).

set -euo pipefail
cd "$(dirname "$0")"

BASE="https://raw.githubusercontent.com/Slicer/Slicer/main"
PRESETS_DIR="Modules/Loadable/VolumeRendering/Resources"

mkdir -p data/icons

fetch() {
    local url="$1" dest="$2"
    if [ -f "$dest" ]; then
        echo "have   $dest"
    else
        echo "fetch  $dest"
        curl -fsSL -o "$dest" "$url"
    fi
}

fetch "$BASE/$PRESETS_DIR/presets.xml" "data/presets.xml"
fetch "$BASE/License.txt"              "data/Slicer-LICENSE.txt"

# Icons mirror preset names 1:1, so list once via the GitHub API.
ICON_LIST=$(curl -fsSL \
    "https://api.github.com/repos/Slicer/Slicer/contents/$PRESETS_DIR/Icons" \
    | python3 -c "import json,sys
for x in json.load(sys.stdin):
    if x['type']=='file' and x['name'].endswith('.png'):
        print(x['name'])")

for name in $ICON_LIST; do
    fetch "$BASE/$PRESETS_DIR/Icons/$name" "data/icons/$name"
done

echo
echo "Done. $(ls data/icons | wc -l | tr -d ' ') preset icons in $(pwd)/data/icons/"
echo "Transfer functions: $(pwd)/data/presets.xml"
