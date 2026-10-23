"""Lokalny panel do podglądu surowych logów ebusd: które linie CSV dopasowują się do których ramek,
jak ebusd rozkłada ramkę na pola, przebiegi wartości w czasie i rejestry bez definicji.
usage: panel.py [--port 8765] [--no-browser] [ebusd_raw.log ...]   (domyślnie ebusd_raw.log i dane_testowe/*.log)"""
import sys, os, re, csv, glob, json, math, time, pickle, bisect, hashlib, argparse, collections, threading, webbrowser
from array import array
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

TOOLS = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
from bridgenet import parse_raw, segments, width
from ebuscsv import load_templates, load_lines, Matcher, decode, ebusd_text

CSV_FILES = ['ariston.csv', 'ariston_nimbus50s.csv']
TEMPLATES = '_templates.csv'
EXTRA = os.path.join(TOOLS, 'extra_registers.csv')
CACHE_DIR = os.path.join(ROOT, '.cache')
CACHE_VERSION = 1
LINE_RE = re.compile(r'(\d{4}-\d\d-\d\d \d\d:\d\d):(\d\d\.\d+) ([<>])([0-9a-f]+)$')
ADDRESSES = {'13': 'menedżer energii', '18': 'menedżer energii', '1e': 'jednostka zewnętrzna',
             '70': 'panel sterujący', '75': 'panel sterujący', '7f': 'bramka Wi-Fi', '84': 'bramka Wi-Fi',
             'fe': 'wszyscy (rozgłoszenie)'}


def log(msg):
    print(time.strftime('%H:%M:%S'), msg, flush=True)


# ---------------------------------------------------------------- warstwa 1: ramki z logów

class Frames:
    """wszystkie poprawne ramki: czas (array t) i indeks unikalnej treści (array u -> uniq[(q, a)])"""

    def __init__(self):
        self.logs, self.t, self.u, self.uniq = [], array('d'), array('I'), []

    @staticmethod
    def cache_path(paths):
        h = hashlib.sha1(str(CACHE_VERSION).encode())
        for p in paths:
            st = os.stat(p); h.update(f'{os.path.abspath(p)}|{st.st_size}|{st.st_mtime_ns}'.encode())
        return os.path.join(CACHE_DIR, f'frames-{h.hexdigest()[:12]}.pickle')

    @classmethod
    def load(cls, paths):
        cp = cls.cache_path(paths)
        if os.path.exists(cp):
            log(f'ramki z pamięci podręcznej {os.path.relpath(cp, ROOT)}')
            with open(cp, 'rb') as fh: return pickle.load(fh)
        fr = cls()
        firsts = []
        for p in paths:
            with open(p, encoding='utf-8', errors='replace') as fh:
                m = next((LINE_RE.match(l.strip()) for l in fh if LINE_RE.match(l.strip())), None)
            firsts.append((m.group(1) if m else '', p))
        umap, minutes = {}, {}
        for _, p in sorted(firsts):                  # logi w kolejności czasu, żeby t rosło
            info = {'file': os.path.relpath(p, ROOT), 'frames': 0, 'bad': 0, 'own': 0, 'first': None, 'last': None}
            log(f'czytam {info["file"]} ...')
            with open(p, encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    m = LINE_RE.match(line.strip())
                    if not m: continue
                    if m.group(3) == '>': info['own'] += 1; continue
                    pr = parse_raw(m.group(4))
                    if not pr: info['bad'] += 1; continue
                    mi = m.group(1)
                    base = minutes.get(mi)
                    if base is None: base = minutes[mi] = time.mktime(time.strptime(mi, '%Y-%m-%d %H:%M'))
                    t = base + float(m.group(2))
                    key = pr[0] + '/' + pr[1]
                    k = umap.get(key)
                    if k is None: k = umap[key] = len(fr.uniq); fr.uniq.append(pr)
                    fr.t.append(t); fr.u.append(k); info['frames'] += 1
                    if info['first'] is None: info['first'] = t
                    info['last'] = t
            fr.logs.append(info)
            log(f'  {info["frames"]} ramek, {info["bad"]} uciętych lub z błędnym CRC')
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cp, 'wb') as fh: pickle.dump(fr, fh, protocol=pickle.HIGHEST_PROTOCOL)
        return fr


# ---------------------------------------------------------------- warstwa 2: dopasowanie do CSV

def extra_notes():
    """rejestr -> (nazwa, komentarz źródłowy) z tools/extra_registers.csv"""
    notes, pending = {}, []
    for line in open(EXTRA, encoding='utf-8'):
        s = line.strip()
        if not s: continue
        if s.startswith('#'):
            if s.startswith('# ['): pending = [s[2:]]
            elif pending: pending.append(s[1:].strip())
            continue
        row = s.split(',')
        notes[row[0].lower()] = (row[2], ' '.join(pending))
    return notes


class Analysis:
    def __init__(self, fr):
        t0 = time.time()
        self.fr = fr
        self.csv_paths = [os.path.join(ROOT, f) for f in CSV_FILES]
        self.mtimes = self.csv_mtimes()
        self.lines = load_lines(self.csv_paths, load_templates(os.path.join(ROOT, TEMPLATES)))
        self.matcher = Matcher(self.lines)
        self.notes = extra_notes()
        n = len(fr.uniq)
        self.uline = array('i', [-1]) * n
        self.unums, self.utext, self.uerr = [None] * n, [None] * n, [None] * n
        self.ushape, self.uregs, ucover = [None] * n, [()] * n, [()] * n   # ucover: rejestry dekodowane przez pola linii
        self.decoded_by = collections.defaultdict(collections.Counter)   # (src, rejestr) -> {(linia, pole): liczba ramek}
        shape_ids = {}
        for k, (q, a) in enumerate(fr.uniq):
            ln = self.matcher.match(q)
            dec = None
            if ln:
                self.uline[k] = ln.idx
                try:
                    dec = decode(ln, q, a)
                    self.unums[k] = tuple(d['value'] if not f.ignored and isinstance(d['value'], (int, float)) else None
                                          for d, f in zip(dec, ln.fields))
                    self.utext[k] = ebusd_text(ln, dec)
                except ValueError as e:
                    self.uerr[k] = str(e)
            segs = segments(q, a)
            sk = (q[:2], q[2:4], q[4:8], ' '.join(s[0] for s in segs if s[1] == 'id') if segs else f'{len(q) // 2 - 5} B danych')
            self.ushape[k] = shape_ids.setdefault(sk, len(shape_ids))
            if segs:
                src = f'{q[:2]}>{q[2:4]} {q[4:8]}'
                data = {'m': bytes.fromhex(q[10:]), 's': bytes.fromhex(a[2:]) if a else b''}
                if q[2:4] != 'fe' and q[4:8] == '2000' and data['s'][:1] == b'\x00':
                    continue                           # maska 00: urządzenie zgłasza dane jako nieważne
                regs, cover = [], []
                for r, role, part, s, e in segs:
                    if role != 'value': continue
                    regs.append(((src, r), int.from_bytes(data[part][s:e], 'little')))
                    for fi, d in enumerate(dec or ()):
                        if d['part'] == part and d['start'] < e and s < d['end'] and not ln.fields[fi].ignored:
                            cover.append(((src, r), (ln.idx, fi)))
                self.uregs[k], ucover[k] = tuple(regs), tuple(cover)
        self.shape_keys = [None] * len(shape_ids)
        for sk, i in shape_ids.items(): self.shape_keys[i] = sk
        log(f'dopasowanie {n} unikalnych ramek: {time.time() - t0:.1f} s')

        t1 = time.time()
        self.line_frames = collections.defaultdict(lambda: array('I'))
        self.reg_frames = collections.defaultdict(lambda: array('I'))
        ucount = array('I', [0]) * n
        U, uline, uregs = fr.u, self.uline, self.uregs
        for i in range(len(U)):
            k = U[i]; ucount[k] += 1
            L = uline[k]
            if L >= 0: self.line_frames[L].append(i)
            for key, _ in uregs[k]: self.reg_frames[key].append(i)
        self.ucount = ucount
        for k in range(n):                     # w ilu ramkach pole linii dekoduje dany rejestr
            for key, lf in ucover[k]: self.decoded_by[key][lf] += ucount[k]
        self.shapes = collections.defaultdict(lambda: {'count': 0, 'lines': collections.Counter(), 'errors': 0, 'uniq': []})
        for k in range(n):
            sh = self.shapes[self.ushape[k]]
            sh['count'] += ucount[k]
            if uline[k] >= 0: sh['lines'][uline[k]] += ucount[k]
            if self.uerr[k]: sh['errors'] += ucount[k]
            if len(sh['uniq']) < 6: sh['uniq'].append(k)
        log(f'indeksy linii i rejestrów: {time.time() - t1:.1f} s')

    def csv_mtimes(self):
        return [os.stat(p).st_mtime_ns for p in self.csv_paths + [os.path.join(ROOT, TEMPLATES), EXTRA]]

    # ---- pomocnicze

    def frame(self, i):
        q, a = self.fr.uniq[self.fr.u[i]]
        return {'t': self.fr.t[i], 'q': q, 'a': a}

    # ---- API

    def summary(self):
        matched = sum(len(v) for v in self.line_frames.values())
        errors = sum(self.ucount[k] for k in range(len(self.fr.uniq)) if self.uerr[k])
        return {'logs': self.fr.logs, 'frames': len(self.fr.t), 'matched': matched, 'errors': errors,
                'unique': len(self.fr.uniq), 'lines': len(self.lines), 'csv': CSV_FILES,
                'first': self.fr.t[0] if self.fr.t else None, 'last': self.fr.t[-1] if self.fr.t else None,
                'addresses': ADDRESSES}

    def line_brief(self, ln, err_count):
        fr = self.line_frames.get(ln.idx)
        last = fr[-1] if fr else None
        return {'idx': ln.idx, 'file': ln.file, 'lineno': ln.lineno, 'kind': ln.kind, 'circuit': ln.circuit,
                'name': ln.name, 'qq': ln.qq, 'zz': ln.zz, 'pbsb': ln.pbsb, 'idh': ln.idh, 'error': ln.error,
                'count': len(fr) if fr else 0, 'errors': err_count.get(ln.idx, 0),
                'first': self.fr.t[fr[0]] if fr else None, 'last': self.fr.t[last] if fr else None,
                'last_text': self.utext[self.fr.u[last]] if fr else None,
                'fields': [f.name or f.type for f in ln.fields if not f.ignored]}

    def lines_list(self):
        err = collections.Counter()
        for k in range(len(self.fr.uniq)):
            if self.uerr[k]: err[self.uline[k]] += self.ucount[k]
        return [self.line_brief(ln, err) for ln in self.lines]

    def field_info(self, f):
        return {'name': f.name, 'part': f.part, 'type': f.type, 'template': f.template, 'size': f.size,
                'divisor': f.divisor, 'values': {str(k): v for k, v in (f.values or {}).items()} or None,
                'unit': f.unit, 'comment': f.comment, 'ignored': f.ignored}

    def decoded_sample(self, i):
        fr = self.frame(i); k = self.fr.u[i]; L = self.uline[k]
        out = dict(fr, text=self.utext[k], error=self.uerr[k])
        if L >= 0 and not self.uerr[k]: out['fields'] = decode(self.lines[L], fr['q'], fr['a'])
        segs = segments(fr['q'], fr['a'])
        out['segments'] = [{'reg': r, 'role': role, 'part': p, 'start': s, 'end': e} for r, role, p, s, e in segs] if segs else None
        return out

    def line_detail(self, L):
        ln = self.lines[L]
        fr = self.line_frames.get(L, array('I'))
        samples, seen = [], set()
        for i in fr:
            key = self.utext[self.fr.u[i]] or self.uerr[self.fr.u[i]]
            if key in seen: continue
            seen.add(key); samples.append(i)
            if len(samples) >= 12: break
        if fr and fr[-1] not in samples: samples.append(fr[-1])
        errs = [i for i in fr if self.uerr[self.fr.u[i]]][:5]
        rivals = []
        for o in self.matcher.candidates('0000' + ln.zz + ln.pbsb):
            if o.idx != L and (o.idh.startswith(ln.idh) or ln.idh.startswith(o.idh)):
                rivals.append({'idx': o.idx, 'name': o.name, 'circuit': o.circuit, 'idh': o.idh, 'qq': o.qq, 'kind': o.kind,
                               'file': o.file, 'lineno': o.lineno, 'count': len(self.line_frames.get(o.idx, ()))})
        cols = next(csv.reader([ln.text]))
        return {'line': self.line_brief(ln, {L: sum(1 for i in fr if self.uerr[self.fr.u[i]])}), 'text': ln.text,
                'columns': cols, 'fields': [self.field_info(f) for f in ln.fields],
                'samples': [self.decoded_sample(i) for i in sorted(samples)], 'error_samples': [self.decoded_sample(i) for i in errs],
                'rivals': rivals,
                'regs': sorted({f'{s}|{r}' for (s, r), c in self.decoded_by.items() for (li, _) in c if li == L})}

    def envelope(self, idx, value, t_from, t_to, n):
        """co najwyżej 2n punktów: min i max w każdym z n przedziałów, w kolejności wystąpienia"""
        T = self.fr.t
        lo = bisect.bisect_left(idx, t_from, key=lambda i: T[i]) if t_from is not None else 0
        hi = bisect.bisect_right(idx, t_to, key=lambda i: T[i]) if t_to is not None else len(idx)
        ts, vs = [], []
        if hi <= lo: return {'t': ts, 'v': vs, 'n': 0}
        a, b = T[idx[lo]], T[idx[hi - 1]]
        span = (b - a) / n or 1
        bucket, bmin, bmax = None, None, None
        def flush():
            if bmin is None: return
            pts = sorted({bmin, bmax})
            for p in pts: ts.append(p[0]); vs.append(p[1])
        for j in range(lo, hi):
            i = idx[j]; v = value(i)
            if v is None: continue
            bk = int((T[i] - a) / span)
            if bk != bucket:
                flush(); bucket, bmin, bmax = bk, (T[i], v), (T[i], v)
            else:
                if v < bmin[1]: bmin = (T[i], v)
                if v > bmax[1]: bmax = (T[i], v)
        flush()
        return {'t': ts, 'v': vs, 'n': hi - lo}

    def line_series(self, L, field, t_from, t_to, n):
        U, nums = self.fr.u, self.unums
        def value(i):
            x = nums[U[i]]
            return x[field] if x else None
        return self.envelope(self.line_frames.get(L, array('I')), value, t_from, t_to, n)

    def reg_series(self, src, reg, signed, t_from, t_to, n):
        U, uregs, key = self.fr.u, self.uregs, (src, reg)
        bits = 8 * width(reg)
        def value(i):
            for kk, v in uregs[U[i]]:
                if kk == key:
                    return v - (1 << bits) if signed and v >= 1 << (bits - 1) else v
            return None
        return self.envelope(self.reg_frames.get(key, array('I')), value, t_from, t_to, n)

    def registers(self):
        out = []
        defined = collections.defaultdict(list)
        for ln in self.lines:
            if len(ln.idh) == 4: defined[ln.idh].append(ln.idx)
        for (src, r), idx in self.reg_frames.items():
            vals = collections.Counter()
            for k in {self.fr.u[i] for i in idx[::max(1, len(idx) // 5000)]}:
                for kk, v in self.uregs[k]:
                    if kk == (src, r): vals[v] += 1
            by = self.decoded_by.get((src, r), {})
            names = sorted({self.lines[li].fields[fi].name or self.lines[li].name for (li, fi) in by})
            note = self.notes.get(r)
            out.append({'src': src, 'reg': r, 'width': width(r), 'count': len(idx), 'distinct': len(vals),
                        'min': min(vals) if vals else None, 'max': max(vals) if vals else None,
                        'names': names, 'decoded': sum(by.values()), 'defined': defined.get(r, []),
                        'note': note[1] if note else '', 'extra_name': note[0] if note else ''})
        return out

    def register_detail(self, src, r):
        by = self.decoded_by.get((src, r), {})
        idx = self.reg_frames.get((src, r), array('I'))
        dec = [{'line': li, 'field': fi, 'name': self.lines[li].name, 'circuit': self.lines[li].circuit,
                'field_name': self.lines[li].fields[fi].name, 'count': c, 'file': self.lines[li].file,
                'lineno': self.lines[li].lineno} for (li, fi), c in by.most_common()]
        other = [{'src': s, 'count': len(v)} for (s, rr), v in self.reg_frames.items() if rr == r and s != src]
        samples = [self.decoded_sample(i) for i in (idx[:1] + idx[-1:])] if idx else []
        defined = [self.line_brief(ln, {}) for ln in self.lines if r in [ln.idh[j:j + 4] for j in range(0, len(ln.idh), 4)]]
        note = self.notes.get(r)
        return {'src': src, 'reg': r, 'width': width(r), 'count': len(idx), 'decoded_by': dec, 'other_sources': other,
                'samples': samples, 'defined': defined, 'note': note[1] if note else '', 'extra_name': note[0] if note else ''}

    def shapes_list(self):
        out = []
        for si, sh in self.shapes.items():
            qq, zz, pbsb, ids = self.shape_keys[si]
            top = sh['lines'].most_common(1)
            out.append({'id': si, 'qq': qq, 'zz': zz, 'pbsb': pbsb, 'ids': ids, 'count': sh['count'],
                        'matched': sum(sh['lines'].values()), 'errors': sh['errors'],
                        'line': top[0][0] if top else None, 'line_name': self.lines[top[0][0]].name if top else None})
        return out

    def shape_detail(self, si):
        sh = self.shapes[si]
        qq, zz, pbsb, ids = self.shape_keys[si]
        samples = []
        for k in sh['uniq'][:4]:
            q, a = self.fr.uniq[k]
            d = {'t': None, 'q': q, 'a': a, 'text': self.utext[k], 'error': self.uerr[k], 'line': int(self.uline[k])}
            if self.uline[k] >= 0 and not self.uerr[k]: d['fields'] = decode(self.lines[self.uline[k]], q, a)
            segs = segments(q, a)
            d['segments'] = [{'reg': r, 'role': role, 'part': p, 'start': s, 'end': e} for r, role, p, s, e in segs] if segs else None
            samples.append(d)
        q0 = self.fr.uniq[sh['uniq'][0]][0]
        cands = []
        for ln in self.matcher.candidates(q0):
            data = q0[10:]
            common = 0
            while common < min(len(data), len(ln.idh)) and data[common] == ln.idh[common]: common += 1
            if data.startswith(ln.idh) and (not ln.qq or ln.qq == q0[:2]): why = 'pasuje'
            elif data.startswith(ln.idh): why = f'inny nadawca: linia wymaga QQ={ln.qq}'
            else: why = f'ID {ln.idh} różni się od danych od znaku {common + 1}'
            cands.append({'idx': ln.idx, 'name': ln.name, 'circuit': ln.circuit, 'idh': ln.idh, 'qq': ln.qq,
                          'common': common, 'why': why, 'file': ln.file, 'lineno': ln.lineno})
        cands.sort(key=lambda c: (c['why'] != 'pasuje', -c['common']))
        names = {}
        for r in ids.split() if ' B ' not in ids else []:
            note = self.notes.get(r)
            src = f'{qq}>{zz} {pbsb}'
            by = self.decoded_by.get((src, r), {})
            nm = sorted({self.lines[li].fields[fi].name or self.lines[li].name for (li, fi) in by})
            defs = sorted({ln.name for ln in self.lines if r in [ln.idh[j:j + 4] for j in range(0, len(ln.idh), 4)]})
            names[r] = {'decoded': nm, 'defined': defs, 'extra': note[0] if note else ''}
        return {'id': si, 'qq': qq, 'zz': zz, 'pbsb': pbsb, 'ids': ids, 'count': sh['count'], 'errors': sh['errors'],
                'lines': [{'idx': li, 'name': self.lines[li].name, 'count': c} for li, c in sh['lines'].most_common()],
                'samples': samples, 'candidates': cands[:12], 'names': names}


# ---------------------------------------------------------------- serwer HTTP

class State:
    frames = None
    analysis = None
    lock = threading.Lock()

    @classmethod
    def current(cls):
        with cls.lock:
            if cls.analysis.csv_mtimes() != cls.analysis.mtimes:
                log('pliki CSV się zmieniły, przeliczam dopasowania')
                cls.analysis = Analysis(cls.frames)
            return cls.analysis


def num(qs, name, default=None, cast=float):
    v = qs.get(name, [None])[0]
    return cast(v) if v not in (None, '') else default


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=os.path.join(TOOLS, 'panel'), **kw)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')     # po edycji plików panelu przeglądarka bierze nową wersję
        super().end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith('/api/'): return super().do_GET()
        qs = parse_qs(url.query)
        try:
            A = State.current()
            ep = url.path[5:]
            if ep == 'summary': out = A.summary()
            elif ep == 'lines': out = A.lines_list()
            elif ep == 'line': out = A.line_detail(num(qs, 'idx', cast=int))
            elif ep == 'series':
                out = A.line_series(num(qs, 'line', cast=int), num(qs, 'field', cast=int), num(qs, 'from'), num(qs, 'to'),
                                    num(qs, 'n', 1500, int))
            elif ep == 'shapes': out = A.shapes_list()
            elif ep == 'shape': out = A.shape_detail(num(qs, 'id', cast=int))
            elif ep == 'registers': out = A.registers()
            elif ep == 'register': out = A.register_detail(qs['src'][0], qs['reg'][0])
            elif ep == 'regseries':
                out = A.reg_series(qs['src'][0], qs['reg'][0], qs.get('signed', ['0'])[0] == '1', num(qs, 'from'),
                                   num(qs, 'to'), num(qs, 'n', 1500, int))
            else:
                return self.send_error(404)
            body = json.dumps(out, ensure_ascii=False, default=float).encode()
            self.send_response(200)
        except Exception as e:                    # błąd pokazuje panel, serwer działa dalej
            body = json.dumps({'error': f'{type(e).__name__}: {e}'}, ensure_ascii=False).encode()
            self.send_response(500)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('logs', nargs='*')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()
    logs = args.logs or [p for p in [os.path.join(ROOT, 'ebusd_raw.log')] + sorted(glob.glob(os.path.join(ROOT, 'dane_testowe', '*.log')))
                         if os.path.exists(p)]
    if not logs: sys.exit('brak logów: podaj ścieżki do surowych logów ebusd (--lograwdata)')
    State.frames = Frames.load(logs)
    State.analysis = Analysis(State.frames)
    srv = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{args.port}/'
    log(f'panel: {url}  (Ctrl+C kończy)')
    if not args.no_browser: threading.Timer(0.5, webbrowser.open, [url]).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == '__main__':
    main()
