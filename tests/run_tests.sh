#!/usr/bin/env bash
# End-to-end test: generates synthetic files with SoulsFormats, runs
# bb_restock.py and checks the result with SoulsFormats.
# Requires: git, python3, .NET SDK 8+.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEPS="$HERE/.deps"
WORK="$HERE/.work"
mkdir -p "$DEPS"

if [ ! -d "$DEPS/SoulsFormatsNEXT" ]; then
  git clone -q --depth 1 https://github.com/soulsmods/SoulsFormatsNEXT "$DEPS/SoulsFormatsNEXT"
fi

# Build SoulsFormats with the installed SDK (the project targets net9.0).
SDK_MAJOR="$(dotnet --version | cut -d. -f1)"
TARGET="net${SDK_MAJOR}.0"
SF_PROJ="$DEPS/SoulsFormatsNEXT/SoulsFormats/SoulsFormats.csproj"
sed -i "s#<TargetFramework>net[0-9.]*</TargetFramework>#<TargetFramework>${TARGET}</TargetFramework>#" "$SF_PROJ"

dotnet build "$HERE/verify/verify.csproj" -nologo -v q \
  -p:SoulsTarget="$TARGET" -p:SoulsFormatsProj="$SF_PROJ" -o "$WORK/bin" >/dev/null

VERIFY="$WORK/bin/verify.dll"

for v in 0 1 2 3; do
  rm -rf "$WORK/t$v"
  dotnet "$VERIFY" gen "$WORK/t$v/game" "$v"
  python3 -I "$HERE/../bb_restock.py" "$WORK/t$v/game" --out "$WORK/t$v/mod" --vials 20 --bullets 15 >/dev/null
  dotnet "$VERIFY" verify "$WORK/t$v/game" "$WORK/t$v/mod" 20 15
done

# Running on already modified files must fail without generating anything.
if python3 -I "$HERE/../bb_restock.py" "$WORK/t0/mod" --out "$WORK/t0/mod2" 2>/dev/null; then
  echo "FAIL: double patch was accepted"; exit 1
fi
echo "ALL TESTS PASSED"
