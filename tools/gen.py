"""Generuje ariston_nimbus50s.csv: linie ebusd dla ramek z surowego logu, których ariston.csv nie rozpoznaje,
oraz linie odczytu dla rejestrów z extra_registers.csv, które mają podany adres urządzenia.
usage: gen.py <ariston.csv> <ebusd_raw.log> <out.csv> [extra_registers.csv]"""
import sys, csv, collections
from bridgenet import read_frames, ids_of, width

csvp, logp, outp = sys.argv[1:4]
MIN_COUNT = 10        # rzadsze układy to jednorazowe zdarzenia (np. przeglądanie menu sterownika)
DOMINANCE = 5         # rejestr dostaje nową linię, gdy układ jest tyle razy częstszy od dotychczasowych źródeł
ONE = {'UCH', 'SCH', 'BCD', 'D1B', 'D1C', 'onoff', 'heat_thermoreg_types', 'cool_thermoreg_types',
       'hybrid_mode', 'heat_request_mode', 'pump_operation', 'error_code'}
TWO = {'UIN', 'SIN', 'D2B', 'D2C'}

# rejestr -> (prio, circuit, name, type, divisor/values, unit); prio 9 = z extra_registers.csv
reg, defs, passive_names, polled = {}, [], set(), {}
prio = {'r': 0, 'b': 1, 'g': 1, 'w': 2}
for row in csv.reader(open(csvp, encoding='utf-8')):
    if not row or row[0].startswith('#') or len(row) < 9: continue
    idh = row[7].lower()
    defs.append((row[4].lower(), row[5].lower(), row[6].lower(), idh))
    if row[0][0] not in 'rw': passive_names.add((row[1], row[2]))
    if len(idh) != 4 or row[1] in ('boiler', 'ignored'): continue
    f = row[8:] + [''] * 6
    val = None
    for i in range(0, len(f) - 5, 6):
        if f[i + 2] and not f[i + 2].upper().startswith('IGN'): val = (f[i + 2], f[i + 3], f[i + 4])
    if not val: continue
    cand = (prio.get(row[0][0], 3), row[1], row[2], *val)
    if idh not in reg or cand[0] < reg[idh][0]: reg[idh] = cand
if len(sys.argv) > 4:
    for row in csv.reader(open(sys.argv[4], encoding='utf-8')):
        if not row or row[0].startswith('#'): continue
        reg[row[0].lower()] = (9, *row[1:6])
        if len(row) > 6 and row[6]: polled[row[0].lower()] = row[6].lower()

covered = collections.Counter()               # rejestr -> ile razy jest już dekodowany
shapes = collections.Counter()                # (qq, zz, pbsb, ids) -> liczba wystąpień nierozpoznanych ramek
partial = {}                                  # (zz, lista rejestrów odczytu) -> rejestry już dekodowane linią o krótszym id
for _, q, a in read_frames(logp):
    line = None
    for qq, zz, pbsb, idh in defs:
        if zz == q[2:4] and pbsb == q[4:8] and q[10:].startswith(idh) and qq in ('', q[:2]):
            if line is None or len(idh) > len(line): line = idh
    ids = ids_of(q, a)
    if line is not None:
        for i in range(0, len(line) - 3, 4): covered[line[i:i + 4]] += 1
        # ebusd dopasowuje po początku danych: linia z krótszym id dekoduje tylko pierwsze rejestry odczytu grupowego,
        # a linia z pełną listą rejestrów ma pierwszeństwo, więc taki układ też jest kandydatem
        if ids and q[2:4] != 'fe' and q[4:8] == '2000' and 0 < len(line) < len(q) - 10:
            shapes[('', q[2:4], '2000', tuple(ids))] += 1
            partial[(q[2:4], tuple(ids))] = {line[i:i + 4] for i in range(0, len(line) - 3, 4)}
    elif ids:
        shapes[(q[:2], q[2:4], q[4:8], tuple(ids))] += 1

def usable(idh):
    r = reg.get(idh)
    size = r and (1 if r[3] in ONE else 2 if r[3] in TWO else None)
    return r if r and size == width(idh) else None
def val(r, part, name=''):
    return f'{name},{part},{r[3]},{r[4]},{r[5]},,'
def ign(n, part):
    return f'ign,{part},IGN:{n},,,,'
def trim(fields):
    while fields and fields[-1].startswith('ign,'): fields.pop()
    return ''.join(fields)

# kandydaci: jedna linia ebusd na grupę ramek, które ebusd dopasuje do tej samej definicji
groups = collections.defaultdict(collections.Counter)  # klucz linii -> {lista rejestrów: liczba}
for (qq, zz, pbsb, ids), n in shapes.items():
    if zz == 'fe': key = ('bc', '', zz, pbsb, ids[0])                 # rozgłoszenia: dopasowanie po pierwszym rejestrze
    elif pbsb == '2020': key = ('wr', qq, zz, pbsb, ids[0])
    else: key = ('rd', '', zz, pbsb, ''.join(ids))                    # odczyty: dopasowanie po całej liście rejestrów
    groups[key][ids] += n

TITLES = {'200e': 'Rozgłoszenia wartość/min/max (200e, odpowiedź na pytanie 2001)',
          'bc': 'Rozgłoszenia wartość+status (2010 cykliczne, 200f odpowiedź na pytanie 2000)',
          'rd': 'Odczyty grupowe podsłuchane między urządzeniami (maska + wartości)',
          '2001': 'Odczyty wartość/min/max (2001) kierowane do urządzenia',
          'single': 'Odczyty rejestrów spoza ariston.csv (odpytywane przez ebusd, dopasowywane też do podsłuchanych odczytów)',
          'wr': 'Zapisy grupowe menedżera energii do jednostki zewnętrznej (2020)'}
sections = collections.defaultdict(list); new_names = set()
def name_for(r, suffix):
    # nazwa jak w linii r z ariston.csv = ten sam temat MQTT; ebusd odrzuca jednak dwie pasywne linie o tej samej nazwie
    name = r[2] if (r[1], r[2]) not in passive_names | new_names else f'{r[2]}_{suffix}'
    if (r[1], name) in passive_names | new_names: return None
    new_names.add((r[1], name)); return name

def common(a, b):
    k = 0
    while k < min(len(a), len(b)) and a[k] == b[k]: k += 1
    return k
def size(ids, step):
    return sum(2 + width(i) + step for i in ids)

for (kind, qq, zz, pbsb, first), variants in sorted(groups.items(), key=lambda kv: -sum(kv[1].values())):
    n = sum(variants.values())
    if n < MIN_COUNT: continue
    # Ta sama linia ebusd obsługuje wszystkie warianty zaczynające się od tego samego rejestru. Bierzemy wariant
    # dominujący, o ile pozostałe są z nim zgodne do ostatniego dekodowanego pola albo za krótkie (ebusd zgłasza
    # wtedy błąd zamiast podać złą wartość); w przeciwnym razie tylko wspólny początek wszystkich wariantów.
    top, top_n = variants.most_common(1)[0]
    pref = list(top)
    for cut in (True, False):
        over = partial.get((zz, tuple(pref)), set()) if kind == 'rd' else set()
        regs = [r if (r := usable(i)) and (i in over or n > DOMINANCE * covered[i]) else None for i in pref]
        if over and not any(r for i, r in zip(pref, regs) if i not in over): regs = [None] * len(pref)
        if kind == 'wr': regs = [r if r and r[0] == 9 else None for r in regs]
        last = max((k for k, r in enumerate(regs) if r), default=-1)
        step = 3 if kind == 'bc' and pbsb != '200e' else 2 if kind == 'wr' else 0
        need = size(pref[:last + 1], step - 2) if step else 0
        safe = top_n >= 0.9 * n and all(common(pref, v) > last or size(v, step - 2) < need for v in variants)
        if safe or not cut or kind == 'rd' or pbsb == '200e': break
        for v in variants: pref = pref[:common(pref, v)]
    live = [r for r in regs if r]
    if not live: continue
    circ = collections.Counter(r[1] for r in live).most_common(1)[0][0]
    ids = ' '.join(pref)
    if kind == 'bc' and pbsb == '200e':
        nm = name_for(regs[0], '200e')
        if not nm: continue
        sections['200e'].append(f'b,{regs[0][1]},{nm},{nm} (200e),,fe,200e,{first},{val(regs[0], "")}')
    elif kind == 'bc' and len(live) == 1 and regs[0]:
        nm = name_for(regs[0], pbsb)
        if not nm: continue
        sections['bc'].append(f'b,{regs[0][1]},{nm},{nm} ({pbsb}),,fe,{pbsb},{first},{val(regs[0], "")}')
    elif kind == 'bc':
        fields = []
        for k, (i, r) in enumerate(zip(pref, regs)):
            if k: fields.append(ign(3, ''))                       # status poprzedniego rejestru + id kolejnego
            fields.append(val(r, '', r[2]) if r else ign(width(i), ''))
        sections['bc'].append(f'b,{circ},bc_{pbsb}_{first},Broadcast {pbsb}: {ids},,fe,{pbsb},{first},{trim(fields)}')
    elif kind == 'wr':
        fields = []
        for k, (i, r) in enumerate(zip(pref, regs)):
            if k: fields.append(ign(2, 'm'))                      # id kolejnego rejestru
            fields.append(val(r, 'm', r[2]) if r else ign(width(i), 'm'))
        sections['wr'].append(f'uw,{circ},set_{first}_{pref[-1]},Group write {ids},{qq},{zz},2020,{first},{trim(fields)}')
    elif pbsb == '2001':
        nm = name_for(regs[0], '2001')
        if not nm: continue
        sections['2001'].append(f'g,{regs[0][1]},{nm},{nm} (2001),,{zz},2001,{first},{val(regs[0], "s")}')
    elif len(pref) == 1:
        if (regs[0][1], regs[0][2]) in new_names: continue
        new_names.add((regs[0][1], regs[0][2]))
        sections['single'].append(f'r,{regs[0][1]},{regs[0][2]},{regs[0][2]},,{zz},2000,{first},{ign(1, "s")}{val(regs[0], "s")}')
    else:
        fields = [ign(1, 's')] + [val(r, 's', r[2]) if r else ign(width(i), 's') for i, r in zip(pref, regs)]
        sections['rd'].append(f'g,{circ},grp_{zz}_{pref[0]}_{pref[-1]},Group read {ids},,{zz},2000,{first},{trim(fields)}')
    for i, r in zip(pref, regs):
        if r: covered[i] += n

for idh, zz in polled.items():          # rejestry do aktywnego odpytywania, niezależnie od tego, co widać w logu
    r = usable(idh)
    if not r or (r[1], r[2]) in new_names or any(d[1] == zz and d[2] == '2000' and d[3] == idh for d in defs): continue
    new_names.add((r[1], r[2]))
    sections['single'].append(f'r,{r[1]},{r[2]},{r[2]},,{zz},2000,{idh},{ign(1, "s")}{val(r, "s")}')

with open(outp, 'w', encoding='utf-8') as fh:
    fh.write('# Uzupełnienie ariston.csv dla Ariston Nimbus 50S (sama pompa ciepła, bez kotła).\n'
             '# Rejestry znane z ariston.csv i tools/extra_registers.csv w układach ramek, które faktycznie\n'
             '# pojawiają się na tej magistrali. Rejestry o nieznanym znaczeniu są pomijane (IGN).\n'
             '# Plik generowany przez tools/gen.py; trzymać w tym samym katalogu co ariston.csv i _templates.csv.\n')
    for key, title in TITLES.items():
        if sections[key]: fh.write(f'\n# {title}\n' + '\n'.join(sorted(sections[key])) + '\n')
print({k: len(v) for k, v in sections.items() if v})
