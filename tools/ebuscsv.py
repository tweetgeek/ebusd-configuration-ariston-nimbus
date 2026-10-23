"""Definicje ebusd z plików CSV: dopasowanie ramek do linii i dekodowanie pól w taki sposób jak ebusd.
Obsługuje typy używane w ariston.csv i ariston_nimbus50s.csv; rzadkie typy czasu są dekodowane w przybliżeniu."""
import csv, math, struct
from dataclasses import dataclass, field

SIZES = {'UCH': 1, 'SCH': 1, 'BCD': 1, 'D1B': 1, 'D1C': 1, 'HEX': 1, 'IGN': 1, 'STR': 1,
         'UIN': 2, 'SIN': 2, 'D2B': 2, 'D2C': 2, 'HTM': 2, 'MIN': 2,
         'VTM': 2, 'HDA': 4, 'ULG': 4, 'SLG': 4}
PASSIVE = ('b', 'g', 'u', 'uw')        # rodzaje linii, które ebusd tylko podsłuchuje


@dataclass
class Field:
    name: str
    part: str            # 'm' = dane mastera, 's' = odpowiedź slave'a
    type: str            # typ bazowy po rozwinięciu szablonu, np. UIN
    size: int            # liczba bajtów
    arg: int | None      # długość/pozycja z zapisu TYP:n (IGN:3, HEX:14, BI0:7)
    divisor: float | None
    values: dict | None
    unit: str
    comment: str
    template: str = ''   # nazwa szablonu z _templates.csv, np. onoff

    @property
    def ignored(self):
        return self.type == 'IGN'


@dataclass
class Line:
    idx: int
    file: str
    lineno: int
    text: str
    kind: str            # r, w, b, g, uw ...
    circuit: str
    name: str
    comment: str
    qq: str
    zz: str
    pbsb: str
    idh: str
    fields: list = field(default_factory=list)
    error: str = ''

    @property
    def passive(self):
        return self.kind in PASSIVE

    @property
    def label(self):
        return f'{self.circuit}.{self.name}'


def _values(s):
    out = {}
    for item in s.split(';'):
        if '=' in item:
            k, v = item.split('=', 1)
            try: out[int(k)] = v
            except ValueError: pass
    return out


def load_templates(path):
    tpl = {}
    for row in csv.reader(open(path, encoding='utf-8')):
        if not row or row[0].strip().startswith('#') or len(row) < 2: continue
        tpl[row[0].strip()] = (row[1].strip(), row[2].strip() if len(row) > 2 else '', row[3].strip() if len(row) > 3 else '')
    return tpl


def _field(name, part, typ, dv, unit, comment, tpl):
    template = ''
    if typ in tpl:
        template = typ
        typ, tdv, tunit = tpl[typ]
        dv, unit = dv or tdv, unit or tunit
    base, _, arg = typ.partition(':')
    arg = int(arg) if arg.isdigit() else None
    if base.startswith('BI') and base[2:].isdigit():
        size = 1
    elif base in SIZES:
        size = arg if base in ('IGN', 'HEX', 'STR', 'HDA') and arg else SIZES[base]
    else:
        raise ValueError(f'nieznany typ {typ}')
    divisor = values = None
    if dv and '=' in dv: values = _values(dv)
    elif dv:
        try: divisor = float(dv)
        except ValueError: pass
    return Field(name, part, base, size, arg, divisor, values, unit, comment, template)


def load_lines(paths, templates):
    """linie z plików CSV (bez komentarzy); linie z błędem definicji mają wypełnione .error"""
    lines = []
    for path in paths:
        fname = path.rsplit('/', 1)[-1]
        for no, text in enumerate(open(path, encoding='utf-8'), 1):
            text = text.rstrip('\n')
            if not text.strip() or text.lstrip().startswith('#'): continue
            row = next(csv.reader([text]))
            if len(row) < 8: continue
            row += [''] * 6
            kind = row[0].strip()
            ln = Line(len(lines), fname, no, text, kind, row[1], row[2], row[3], row[4].lower(), row[5].lower(),
                      row[6].lower(), row[7].lower())
            default = 'm' if kind.startswith(('w', 'b', 'uw')) else 's'
            broadcast = ln.zz == 'fe'          # rozgłoszenie nie ma odpowiedzi: ebusd czyta wszystkie pola z danych mastera
            try:
                for i in range(8, len(row) - 5, 6):
                    name, part, typ, dv, unit, comment = (c.strip() for c in row[i:i + 6])
                    if not typ: continue
                    ln.fields.append(_field(name, 'm' if broadcast else part or default, typ, dv, unit, comment, templates))
            except ValueError as e:
                ln.error = str(e)
            lines.append(ln)
    return lines


class Matcher:
    """ebusd szuka linii po QQ (jeśli podany), ZZ, PBSB i początku danych mastera; wygrywa najdłuższe ID"""

    def __init__(self, lines):
        self.by_key = {}
        for ln in lines:
            if ln.error or not ln.pbsb: continue
            self.by_key.setdefault((ln.zz, ln.pbsb), []).append(ln)
        for lst in self.by_key.values():
            # przy równej długości ID linia pasywna (b/g/uw) wygrywa z odczytem aktywnym
            lst.sort(key=lambda ln: (-len(ln.idh), not ln.passive, ln.idx))

    def candidates(self, q):
        return self.by_key.get((q[2:4], q[4:8]), [])

    def match(self, q):
        data = q[10:]
        for ln in self.candidates(q):
            if data.startswith(ln.idh) and (not ln.qq or ln.qq == q[:2]):
                return ln
        return None


def _num(f, b):
    t = f.type
    if t == 'UCH': return b[0]
    if t in ('SCH', 'D1B'): return b[0] - 256 if b[0] > 127 else b[0]
    if t == 'D1C': return b[0] / 2
    if t == 'BCD':
        hi, lo = b[0] >> 4, b[0] & 15
        if hi > 9 or lo > 9: raise ValueError('argument value out of valid range')
        return hi * 10 + lo
    if t.startswith('BI'):
        return (b[0] >> int(t[2:])) & ((1 << (f.arg or 1)) - 1)
    v = int.from_bytes(b, 'little')
    if t in ('UIN', 'ULG'): return v
    if t in ('SIN', 'SLG'):
        bits = 8 * len(b)
        return v - (1 << bits) if v >= 1 << (bits - 1) else v
    if t == 'D2B': return (v - 65536 if v > 32767 else v) / 256
    if t == 'D2C': return (v - 65536 if v > 32767 else v) / 16
    return None


def decode_field(f, b):
    """(wartość liczbowa albo None, tekst jak w ebusd)"""
    t = f.type
    if t == 'IGN': return None, ''
    if t == 'HEX': return None, ' '.join(f'{x:02x}' for x in b)
    if t == 'STR': return None, b.decode('latin-1').rstrip('\x00 ')
    if t == 'HTM': return None, f'{b[0]:02d}:{b[1]:02d}'
    if t == 'MIN':
        m = int.from_bytes(b, 'little'); return m, f'{m // 60:02d}:{m % 60:02d}'
    if t == 'VTM': return None, f'{b[1]:02d}:{b[0]:02d}'
    if t == 'HDA': return None, f'{b[0]:02d}.{b[1]:02d}.20{b[-1]:02d}'
    v = _num(f, b)
    if v is None: return None, '-'
    if f.values is not None:
        return v, f.values.get(v, str(v))
    if f.divisor:
        if f.divisor < 0: v = v * -f.divisor; return v, str(v)
        v = struct.unpack('f', struct.pack('f', v / f.divisor))[0]     # ebusd liczy na float32
        prec = math.ceil(math.log10(f.divisor)) if f.divisor > 1 else 0
        return v, f'{v:.{prec}f}'
    return v, str(v)


def decode(ln, q, a):
    """pola linii dla ramki: lista dict(field, part, start, end, raw, value, text) albo wyjątek ValueError
    (jak 'invalid position' w ebusd), gdy pole wychodzi poza dane"""
    data = {'m': bytes.fromhex(q[10:]), 's': bytes.fromhex(a[2:]) if a else b''}
    pos = {'m': len(ln.idh) // 2, 's': 0}
    out = []
    for i, f in enumerate(ln.fields):
        p = f.part
        start, end = pos[p], pos[p] + f.size
        if end > len(data[p]):
            raise ValueError(f'invalid position: pole {i + 1} ({f.name or f.type}) potrzebuje bajtów '
                             f'{start}-{end - 1} danych {"mastera" if p == "m" else "slave"}, a jest ich {len(data[p])}')
        b = data[p][start:end]
        try: v, txt = decode_field(f, b)
        except ValueError as e: raise ValueError(f"{e}: pole {i + 1} ({f.name or f.type}) = {b.hex()}")
        out.append({'field': i, 'part': p, 'start': start, 'end': end, 'raw': b.hex(), 'value': v, 'text': txt})
        pos[p] = end
    return out


def ebusd_text(ln, decoded):
    """wartości tak, jak ebusd wypisuje je w logu: nieignorowane pola rozdzielone średnikiem"""
    return ';'.join(d['text'] for d, f in zip(decoded, ln.fields) if not f.ignored)
