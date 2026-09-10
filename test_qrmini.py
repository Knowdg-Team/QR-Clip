"""Verification de l'encodeur QR : vecteur connu, propriete algebrique, aller-retour."""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qrmini as q

FAILED = []

def check(nom, ok, detail=None):
    print("%-45s %s%s" % (nom + " :", "OK" if ok else "ECHEC",
                          "" if ok or detail is None else " %s" % (detail,)))
    if not ok:
        FAILED.append(nom)


# --- 1. vecteur connu : "HELLO WORLD" version 1-Q (exemple de reference thonky)
data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236]
expected = [168, 72, 22, 82, 217, 54, 156, 0, 46, 15, 180, 122, 16]
got = q._rs_encode(data, 13)
check("RS vecteur connu", got == expected, got)

# --- 2. propriete mathematique : data||ec divisible par le generateur
#     => le polynome s'annule en alpha^0 .. alpha^(n-1)
def divisible(msg, ecn):
    full = list(msg) + list(q._rs_encode(msg, ecn))
    for i in range(ecn):
        acc = 0
        for c in full:
            acc = q._gf_mul(acc, q._EXP[i]) ^ c
        if acc != 0:
            return False
    return True
ok = all(divisible([random.randrange(256) for _ in range(random.randrange(5, 100))], ecn)
         for ecn in (7, 10, 13, 17, 18, 20, 22, 24, 26, 28, 30) for _ in range(20))
check("RS divisibilite (220 tirages)", ok)

# --- 3. decodeur de controle : relit la matrice comme le ferait un lecteur
def decode(m):
    size = len(m)
    version = (size - 17) // 4
    # a) relire les infos de format (1re copie) pour retrouver niveau + masque
    bits = 0
    for i in range(6):
        bits |= m[i][8] << i
    bits |= m[7][8] << 6
    bits |= m[8][8] << 7
    bits |= m[8][7] << 8
    for i in range(9, 15):
        bits |= m[8][14 - i] << i
    bits ^= 0x5412
    # verifier le BCH
    rem = bits
    for i in range(14, 9, -1):
        if rem >> i & 1:
            rem ^= 0x537 << (i - 10)
    assert rem == 0, "format info : BCH invalide"
    fmt = bits >> 10
    ecl = {1: 'L', 0: 'M', 3: 'Q', 2: 'H'}[fmt >> 3]
    mask = fmt & 7

    # b) rejouer les motifs fixes pour savoir ou sont les donnees
    c = q._Canvas(version)
    c.draw_function_patterns()
    fixed = c.fixed

    # c) demasquer + relire le zigzag
    bitlist = []
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = (size - 1 - vert) if upward else vert
                if not fixed[y][x]:
                    v = m[y][x]
                    if q._MASKS[mask](x, y):
                        v ^= 1
                    bitlist.append(v)
        right -= 2
    cw = [int(''.join(map(str, bitlist[i:i+8])), 2) for i in range(0, len(bitlist) - 7, 8)]

    # d) de-entrelacer
    ec_len, groups = q.ECC[(version, ecl)]
    sizes = [dc for nb, dc in groups for _ in range(nb)]
    blocks = [[] for _ in sizes]
    idx = 0
    for i in range(max(sizes)):
        for b, s in enumerate(sizes):
            if i < s:
                blocks[b].append(cw[idx]); idx += 1
    stream = [b for blk in blocks for b in blk]

    # e) lire l'en-tete mode octet
    bs = ''.join(format(b, '08b') for b in stream)
    assert bs[:4] == '0100', "mode inattendu %s" % bs[:4]
    ln_bits = 8 if version < 10 else 16
    n = int(bs[4:4 + ln_bits], 2)
    start = 4 + ln_bits
    payload = bytes(int(bs[start + 8*i: start + 8*i + 8], 2) for i in range(n))
    return payload.decode('utf-8'), version, ecl, mask

tests = ["http://192.168.1.42:8765/?k=8f2a1c", "a", "x" * 110,
         "Presse-papier : accents ok ? éàü — ok", "https://exemple.local:8765/?k=" + "z"*40]
allok = True
for t in tests:
    for lvl in ('L', 'M', 'Q', 'H'):
        try:
            txt, v, e, mk = decode(q.encode(t, lvl))
        except Exception as ex:
            print("  echec (%s, %s) : %s" % (t[:20], lvl, ex)); allok = False; continue
        if txt != t or e != lvl:
            print("  echec round-trip (%s, %s) -> %r" % (t[:20], lvl, txt[:20])); allok = False
check("Round-trip encodeur/decodeur (20 cas)", allok)

# --- 4. controles de structure sur un cas reel
m = q.encode("http://192.168.1.42:8765/?k=8f2a1c")
size = len(m)
check("Module noir obligatoire", m[size-8][8] == 1)
tim = all(m[6][i] == (1 - i % 2) for i in range(8, size-8))
check("Motif de synchronisation", tim)
fin = all(m[y][x] == v for (y, x, v) in [(0,0,1),(1,1,0),(2,2,1),(3,3,1),(7,7,0)])
check("Motif de detection", fin)
png = q.to_png(m)
check("PNG (%d octets)" % len(png), png[:8] == b'\x89PNG\r\n\x1a\n')

sys.exit(1 if FAILED else 0)
