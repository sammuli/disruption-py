#!/usr/bin/env bash
#
# Build and install the fdp:// MDSplus transport (libMdsIpFDP.so) from source.
#
# This is all disruption-py needs to read DIII-D data over the Fusion Data
# Platform: one shared library dropped next to your MDSplus libraries. No conda,
# no pixi, and none of the FDP data-access stack (toksearch, ptdata, XRootD).
#
# Requirements: cmake, a C++ compiler, libcurl headers, and an existing MDSplus
# installation. MDSplus headers are NOT needed -- the source repo vendors them.
#
# Usage:
#   scripts/install_mdsip_fdp.sh                 # auto-detect MDSplus
#   MDSPLUS_DIR=/usr/local/mdsplus scripts/install_mdsip_fdp.sh
#   SRC=~/src/xrdoss-mdsplus scripts/install_mdsip_fdp.sh   # reuse a checkout
#
set -euo pipefail

REPO_URL="https://github.com/GA-FDP/xrdoss-mdsplus.git"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- locate MDSplus -----------------------------------------------------------
# The transport links libMdsIpShr.so, and MDSplus dlopens it from beside
# libMdsShr.so (which has RPATH $ORIGIN/.), so both must live in the same dir.
find_mdsplus_lib() {
    local d
    for d in ${MDSPLUS_DIR:+"$MDSPLUS_DIR/lib"} \
             ${CONDA_PREFIX:+"$CONDA_PREFIX/lib"} \
             /usr/local/mdsplus/lib /usr/lib64 /usr/lib /usr/local/lib; do
        [ -f "$d/libMdsIpShr.so" ] && [ -f "$d/libMdsShr.so" ] && { echo "$d"; return; }
    done
    return 1
}

MDS_LIB="$(find_mdsplus_lib)" || {
    echo "ERROR: could not find libMdsIpShr.so and libMdsShr.so together." >&2
    echo "       Install MDSplus (https://www.mdsplus.org) and set MDSPLUS_DIR." >&2
    exit 1
}
echo "MDSplus libraries: $MDS_LIB"

# --- source -------------------------------------------------------------------
if [ -n "${SRC:-}" ]; then
    echo "Using existing checkout: $SRC"
else
    SRC="$WORK/xrdoss-mdsplus"
    echo "Cloning $REPO_URL"
    git clone --depth 1 "$REPO_URL" "$SRC"
fi

# --- build (client only; the relay and OSS plugins need XRootD, we do not) -----
echo "Building libMdsIpFDP.so"
cmake -S "$SRC" -B "$WORK/build" \
    -DBUILD_CLIENT=ON -DBUILD_RELAY=OFF -DBUILD_OSS=OFF -DBUILD_TESTS=OFF \
    -DCMAKE_PREFIX_PATH="${MDS_LIB%/lib}" >/dev/null
cmake --build "$WORK/build" -j"$(nproc)" >/dev/null

# --- install ------------------------------------------------------------------
# The name must be exactly libMdsIpFDP.so: MDSplus builds the image name as
# "MdsIp" + uppercased scheme and dlopens it. A version suffix will not be found.
DEST="$MDS_LIB/libMdsIpFDP.so"
if [ -w "$MDS_LIB" ]; then
    install -m 0755 "$WORK/build/libMdsIpFDP.so" "$DEST"
else
    echo "  (need elevated permissions to write $MDS_LIB)"
    sudo install -m 0755 "$WORK/build/libMdsIpFDP.so" "$DEST"
fi
echo "Installed: $DEST"

# --- verify -------------------------------------------------------------------
# MDSplus silently falls back to the ssh-tunnel transport when it cannot load a
# protocol, so a failed connection does NOT tell you the transport is missing.
# Ask LoadIo directly instead.
PY="${PYTHON:-python3}"
if "$PY" -c "import MDSplus" 2>/dev/null; then
    "$PY" - <<'PYEOF'
import ctypes, sys
from MDSplus.version import load_library

lib = load_library("MdsIpShr")
lib.LoadIo.restype = ctypes.c_void_p
lib.LoadIo.argtypes = [ctypes.c_char_p]
fallback, tcp, fdp = (lib.LoadIo(p) for p in (b"no-such-protocol", b"tcp", b"fdp"))
if not fallback or not tcp or tcp == fallback:
    sys.exit("VERIFY FAILED: MDSplus cannot load any transport here.")
if fdp == fallback:
    sys.exit("VERIFY FAILED: fdp:// resolved to the fallback -- the library was not found.")
print("Verified: MDSplus resolves the fdp:// transport.")
PYEOF
else
    echo "NOTE: '$PY -c \"import MDSplus\"' failed, so resolution was not verified."
    echo "      Put the MDSplus Python bindings on PYTHONPATH and re-run to check."
fi

cat <<'EOF2'

Next:
  1. Get a token (in a real terminal -- pelican needs a TTY for new consent):
       pelican credentials token get read pelican://osg-htc.org:443/fdp-d3d
       export BEARER_TOKEN="<the JWT it prints>"
  2. Point disruption-py at the origin; see docs/usage/fdp.md.
EOF2
