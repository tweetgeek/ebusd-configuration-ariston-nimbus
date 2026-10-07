"""Wypisuje przykładowe ramki z surowego logu ebusd w formacie dla `ebusd --inject`
(po kilka na każdy układ rejestrów), do weryfikacji konfiguracji przez tools/runall.sh.
usage: frames.py <ebusd_raw.log>"""
import sys, collections
from bridgenet import read_frames, ids_of

PER_SHAPE = 3
seen = collections.Counter(); out = {}
for _, q, a in read_frames(sys.argv[1]):
    ids = ids_of(q, a)
    shape = q[:8] + (''.join(ids) if ids else f'len{len(q)}')
    if seen[shape] < PER_SHAPE and q + '/' + a not in out:
        out[q + '/' + a] = 1; seen[shape] += 1
print('\n'.join(out))
