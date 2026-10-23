"""Wspólne funkcje do czytania surowego logu ebusd (--lograwdata) z magistrali Bridgenet."""
import re


def width(idh):
    """szerokość wartości rejestru w bajtach: rejestry 4xxx-7xxx są 16-bitowe, pozostałe 8-bitowe"""
    return 2 if idh[0] in '4567' else 1


def crc8(bs):
    crc = 0
    for b in bs:
        for b in ((0xa9, 0x00) if b == 0xa9 else (0xa9, 0x01) if b == 0xaa else (b,)):   # CRC liczone po rozwinięciu escape
            for _ in range(8):
                poly = 0x9b if crc & 0x80 else 0
                crc = ((crc & 0x7f) << 1) | (1 if b & 0x80 else 0)
                crc ^= poly
                b = (b << 1) & 0xff
    return crc


def crc_ok(h):
    return crc8(bytes.fromhex(h[:-2])) == int(h[-2:], 16)


def parse_raw(h):
    """surowa ramka -> (master bez CRC, odpowiedź NN+dane) albo None, gdy ucięta lub z błędnym CRC"""
    if len(h) < 12: return None
    try:
        zz, n = h[2:4], int(h[8:10], 16)
        q, rest = h[:10 + 2 * n], h[12 + 2 * n:]
        if len(q) != 10 + 2 * n or not crc_ok(h[:12 + 2 * n]): return None
        if zz == 'fe': return q, ''
        if rest[:2] != '00': return None
        n2 = int(rest[2:4], 16); a = rest[2:4 + 2 * n2]
        if len(a) != 2 + 2 * n2 or not crc_ok(rest[2:6 + 2 * n2]): return None
        return q, a
    except ValueError:
        return None


def read_frames(path):
    """poprawne ramki odebrane z magistrali; linie z '>' to własne zapytania ebusd i są pomijane"""
    for ln in open(path, encoding='utf-8', errors='replace'):
        m = re.match(r'(\S+ \S+) <([0-9a-f]+)$', ln.strip())
        p = m and parse_raw(m.group(2))
        if p: yield m.group(1), p[0], p[1]


def ids_of(q, a=''):
    """lista rejestrów w ramce albo None, gdy ramka nie niesie rejestrów w znanym układzie"""
    zz, pbsb, d = q[2:4], q[4:8], q[10:]
    if zz != 'fe' and pbsb in ('2000', '2001'):
        if len(d) % 4: return None
        ids = [d[i:i + 4] for i in range(0, len(d), 4)]
        if pbsb == '2000': return ids if len(a) == 4 + 2 * sum(map(width, ids)) else None
        return ids if len(ids) == 1 and len(a) == 2 + 6 * width(d) else None
    if zz == 'fe' and pbsb == '200e':
        return [d[:4]] if len(d) == 4 + 6 * width(d[:4]) else None
    step = 2 if zz == 'fe' and pbsb in ('2010', '200f') else 0 if zz != 'fe' and pbsb == '2020' else None
    if step is None: return None
    ids, i = [], 0
    while i + 4 <= len(d):
        ids.append(d[i:i + 4]); i += 4 + 2 * width(d[i:i + 4]) + step
    return ids if ids and i == len(d) else None


def segments(q, a=''):
    """podział ramki na pola rejestrów: lista (rejestr, rola, część, bajt od, bajt do) z pozycjami liczonymi od
    początku danych mastera ('m') albo odpowiedzi ('s'); role: id, value, min, max, status, mask. None, gdy
    ramka nie niesie rejestrów w znanym układzie"""
    ids = ids_of(q, a)
    if not ids: return None
    zz, pbsb = q[2:4], q[4:8]
    if zz != 'fe' and pbsb == '2000':
        out, i = [(r, 'id', 'm', 2 * k, 2 * k + 2) for k, r in enumerate(ids)] + [(None, 'mask', 's', 0, 1)], 1
        for r in ids:
            out.append((r, 'value', 's', i, i + width(r))); i += width(r)
        return out
    if zz != 'fe' and pbsb == '2001':
        r, w = ids[0], width(ids[0])
        return [(r, 'id', 'm', 0, 2)] + [(r, role, 's', k * w, k * w + w) for k, role in enumerate(('value', 'min', 'max'))]
    if pbsb == '200e':
        r, w = ids[0], width(ids[0])
        return [(r, 'id', 'm', 0, 2)] + [(r, role, 'm', 2 + k * w, 2 + (k + 1) * w) for k, role in enumerate(('value', 'min', 'max'))]
    out, step, i = [], (1 if zz == 'fe' else 0), 0
    for r in ids:
        w = width(r)
        out += [(r, 'id', 'm', i, i + 2), (r, 'value', 'm', i + 2, i + 2 + w)]
        if step: out.append((r, 'status', 'm', i + 2 + w, i + 3 + w))
        i += 2 + w + step
    return out
