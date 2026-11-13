# shellcheck shell=bash
# Source this from any script that launches NTrader (`uv run ...`, pytest, uvicorn):
#
#     source "$(dirname "${BASH_SOURCE[0]}")/glibc_env.sh"   # from a script in scripts/
#     source scripts/glibc_env.sh                             # after cd to the repo root
#
# glibc >= 2.41 (Ubuntu 26.04 ships 2.43) refuses to dlopen a shared object that asks
# for an executable stack, and nautilus_trader's compiled extensions do:
#
#     ImportError: .../nautilus_trader/backtest/engine.cpython-311-x86_64-linux-gnu.so:
#     cannot enable executable stack as shared object requires: Invalid argument
#
# glibc.rtld.execstack=2 tells the dynamic loader to allow it. glibc reads
# GLIBC_TUNABLES once, when the process starts, so it has to be in the environment of
# every process that imports the engine; setting it from inside Python is too late.
#
# Linux only. macOS has no glibc, and glibc older than 2.41 silently ignores a tunable
# it does not know, so this is a no-op everywhere it is not needed. An existing
# execstack setting is left alone, and any other GLIBC_TUNABLES entries are kept.
if [ "$(uname -s)" = "Linux" ]; then
    case "${GLIBC_TUNABLES:-}" in
        *glibc.rtld.execstack*) ;;
        *) export GLIBC_TUNABLES="${GLIBC_TUNABLES:+${GLIBC_TUNABLES}:}glibc.rtld.execstack=2" ;;
    esac
fi
