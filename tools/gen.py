"""Generuje uzupełniający CSV ebusd z ramek, których ariston.csv nie rozpoznaje.
usage: gen.py <ariston.csv> <base.out (wyjście ebusd dla ramek z logu)> <log ebusd> <out.csv> [extra_registers.csv]"""
import sys, re, csv, collections

csvp, basep, logp, outp = sys.argv[1:5]
ONE = {'UCH', 'SCH', 'BCD', 'D1B', 'D1C', 'onoff', 'heat_thermoreg_types', 'cool_thermoreg_types',
       'hybrid_mode', 'heat_request_mode', 'pump_operation', 'error_code'}
TWO = {'UIN', 'SIN', 'D2B', 'D2C'}
def tsize(t):
    return 1 if t in ONE else 2 if t in TWO else None
def width(idh):
    return 2 if idh[0] in '67' else 1

# rejestr -> (prio, circuit, name, type, divisor/values, unit); ids rozpoznawane już pasywnie
reg, line_ids, passive_names = {}, {}, set()
prio = {'r': 0, 'b': 1, 'g': 1, 'w': 2}
for row in csv.reader(open(csvp, encoding='utf-8')):
    if not row or row[0].startswith('#') or len(row) < 9: continue
    idh = row[7].lower()
    line_ids.setdefault((row[1], row[2]), set()).update(idh[i:i + 4] for i in range(0, len(idh) - 3, 4))
    if row[0][0] not in 'rw': passive_names.add((row[1], row[2]))
    if len(idh) != 4 or row[1] in ('boiler', 'ignored'): continue
    f = row[8:] + [''] * 6
    val = None
    for i in range(0, len(f) - 5, 6):
        if f[i + 2] and not f[i + 2].upper().startswith('IGN'): val = (f[i + 2], f[i + 3], f[i + 4])
    if not val: continue
    cand = (prio.get(row[0][0], 3), row[1], row[2], *val)
    if idh not in reg or cand[0] < reg[idh][0]: reg[idh] = cand

if len(sys.argv) > 5:                  # rejestry opisane poza ariston.csv; nie nadpisują istniejących
    for row in csv.reader(open(sys.argv[5], encoding='utf-8')):
        if row and not row[0].startswith('#'):
            reg.setdefault(row[0].lower(), (9, *row[1:6]))

unknown, covered = set(), set()
for ln in open(basep, encoding='utf-8', errors='replace'):
    m = re.search(r'received unknown (MS|BC) cmd: ([0-9a-f]+)(?: / ([0-9a-f]*))?', ln)
    if m: unknown.add((m.group(1), m.group(2), m.group(3) or '')); continue
    m = re.search(r'received (?:update-)?(?:read|write) (\S+) ([^\s:]+)[ :]', ln)
    if m: covered |= line_ids.get((m.group(1), m.group(2)), set())
freq = collections.Counter()           # jak często dany układ ramki występuje w logu
for ln in open(logp, encoding='utf-8', errors='replace'):
    m = re.search(r'unknown (?:MS|BC) cmd: ([0-9a-f]+)', ln)
    if m: freq[m.group(1)[:8] + m.group(1)[10:]] += 1

ms_read, ms_minmax, bc_seq, bc_minmax = {}, set(), collections.defaultdict(list), set()
for kind, q, a in unknown:
    qq, zz, pbsb, n, d = q[:2], q[2:4], q[4:8], int(q[8:10], 16), q[10:]
    if len(d) != 2 * n: continue
    if kind == 'MS' and pbsb == '2000' and zz == '1e' and len(d) % 4 == 0:
        ids = [d[i:i + 4] for i in range(0, len(d), 4)]
        if len(ids) > 1 and len(a) == 4 + 2 * sum(map(width, ids)):
            ms_read[d] = (ids, ms_read.get(d, (0, 0))[1] + freq[q[:8] + d])
    elif kind == 'MS' and pbsb == '2001' and len(d) == 4 and len(a) == 2 + 6 * width(d):
        ms_minmax.add((zz, d))
    elif kind == 'BC' and pbsb in ('2010', '200f'):
        ids, i = [], 0
        while i + 4 <= len(d):
            ids.append(d[i:i + 4]); i += 4 + 2 * width(d[i:i + 4]) + 2
        if i == len(d): bc_seq[(pbsb, ids[0])].append(ids)
    elif kind == 'BC' and pbsb == '200e' and len(d) == 4 + 6 * width(d[:4]):
        bc_minmax.add(d[:4])

def usable(idh):
    r = reg.get(idh)
    return r if r and tsize(r[3]) == width(idh) and idh not in covered else None
def val(r, part, name=''):
    return f'{name},{part},{r[3]},{r[4]},{r[5]},,'
def ign(n, part):
    return f'ign,{part},IGN:{n},,,,'
def trim(fields):
    while fields and fields[-1].startswith('ign,'): fields.pop()
    return ''.join(fields)

sections, new_names = [], set()
def single_name(r, pbsb):
    # nazwa jak w linii r z ariston.csv = ten sam temat MQTT; ebusd odrzuca jednak dwie pasywne linie o tej samej nazwie
    name = r[2] if (r[1], r[2]) not in passive_names | new_names else f'{r[2]}_{pbsb}'
    if (r[1], name) in passive_names | new_names: return None
    new_names.add((r[1], name)); return name

sec = []
for idh in sorted(bc_minmax):
    r = usable(idh)
    nm = r and single_name(r, '200e')
    if nm: sec.append(f'b,{r[1]},{nm},{nm} (200e),,fe,200e,{idh},{val(r, "")}'); covered.add(idh)
sections.append(('Rozgłoszenia wartość/min/max (200e, odpowiedź na pytanie 2001)', sec))

sec = []
for (pbsb, first), seqs in sorted(bc_seq.items()):
    pref = seqs[0]
    for s in seqs[1:]:
        k = 0
        while k < min(len(pref), len(s)) and pref[k] == s[k]: k += 1
        pref = pref[:k]
    regs = [usable(i) for i in pref]
    live = [r for r in regs if r]
    if not live: continue
    if len(live) == 1 and regs[0]:
        nm = single_name(regs[0], pbsb)
        if nm: sec.append(f'b,{regs[0][1]},{nm},{nm} ({pbsb}),,fe,{pbsb},{first},{val(regs[0], "")}'); covered.add(first)
        continue
    fields = []
    for k, (i, r) in enumerate(zip(pref, regs)):
        if k: fields.append(ign(3, ''))                       # status poprzedniego rejestru + id kolejnego
        fields.append(val(r, '', r[2]) if r else ign(width(i), ''))
    c = collections.Counter(r[1] for r in live).most_common(1)[0][0]
    sec.append(f'b,{c},bc_{pbsb}_{first},Broadcast {pbsb}: {" ".join(pref)},,fe,{pbsb},{first},{trim(fields)}')
    covered.update(i for i, r in zip(pref, regs) if r)
sections.append(('Rozgłoszenia wartość+status (2010 cykliczne, 200f odpowiedź na pytanie 2000)', sec))

sec = []
for d, (ids, n) in sorted(ms_read.items(), key=lambda kv: -kv[1][1]):
    regs = [usable(i) for i in ids]
    if not any(regs): continue
    fields = [ign(1, 's')] + [val(r, 's', r[2]) if r else ign(width(i), 's') for i, r in zip(ids, regs)]
    sec.append(f'g,heatpump,grp_{ids[0]}_{ids[-1]},Group read {" ".join(ids)},,1e,2000,{d},{trim(fields)}')
    covered.update(i for i, r in zip(ids, regs) if r)
sections.append(('Odczyty grupowe menedżera energii z jednostki zewnętrznej (maska + wartości)', sec))

sec = []
for zz, idh in sorted(ms_minmax):
    r = usable(idh)
    nm = r and single_name(r, '2001')
    if nm: sec.append(f'g,{r[1]},{nm},{nm} (2001),,{zz},2001,{idh},{val(r, "s")}'); covered.add(idh)
sections.append(('Odczyty wartość/min/max (2001) kierowane do urządzenia', sec))

with open(outp, 'w', encoding='utf-8') as fh:
    fh.write('# Uzupełnienie ariston.csv dla Ariston Nimbus 50S (sama pompa ciepła, bez kotła).\n'
             '# Rejestry znane z ariston.csv, ale w układach ramek, które faktycznie pojawiają się\n'
             '# na tej magistrali. Każdy rejestr jest dekodowany tylko w jednej linii, a rejestry\n'
             '# o nieznanym znaczeniu są pomijane (IGN).\n'
             '# Trzymać w tym samym katalogu co ariston.csv i _templates.csv.\n')
    for title, sec in sections:
        if sec: fh.write(f'\n# {title}\n' + '\n'.join(sec) + '\n')
print({t.split(' (')[0]: len(s) for t, s in sections})
