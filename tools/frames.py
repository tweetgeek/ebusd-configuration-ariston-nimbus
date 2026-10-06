import sys, re
seen = {}
for ln in open(sys.argv[1], encoding='utf-8', errors='replace'):
    m = re.search(r'unknown (MS|BC|MM) cmd: ([0-9a-f]+)(?: / ([0-9a-f]*))?', ln)
    if not m: continue
    fr = m.group(2) + '/' + (m.group(3) or '')
    seen[fr] = seen.get(fr, 0) + 1
for fr, n in seen.items(): print(fr)
