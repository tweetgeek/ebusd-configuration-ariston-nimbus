#!/bin/bash
# usage: runall.sh <cfgdir> <frames.txt> -> full ebusd output for all frames (chunks of 400)
cfg="$1"; fl="$2"
split -l 400 "$fl" "$fl.part."
for p in "$fl".part.*; do
  docker run --rm -v "$cfg:/cfg:ro" --entrypoint ebusd john30/ebusd:latest -f --device=/dev/null --nodevicecheck --configpath=/cfg --scanconfig=none --log=all:notice --inject=stop $(cat "$p") 2>&1
done
rm -f "$fl".part.*
