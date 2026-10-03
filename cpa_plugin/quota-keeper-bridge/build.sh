#!/bin/sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
docker run --rm -v "$DIR:/src" -w /src golang:1.26-bookworm sh -c '/usr/local/go/bin/go build -buildvcs=false -buildmode=c-shared -o quota-keeper-bridge.so .'
rm -f "$DIR/quota-keeper-bridge.h"
echo "$DIR/quota-keeper-bridge.so"
