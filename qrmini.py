"""Encodeur QR minimal (mode octet, versions 1 a 10) sans aucune dependance.

Fournit :
  encode(text, ecl='M')       -> matrice de 0/1 (liste de listes, [ligne][colonne])
  to_png(matrix, ...)         -> octets d'un PNG
  to_ansi(matrix, ...)        -> chaine coloree pour terminal
"""

# --- tables du standard (versions 1 a 10) ---------------------------------

TOTAL_CW = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134,
            6: 172, 7: 196, 8: 242, 9: 292, 10: 346}

ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
         6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
         10: [6, 28, 50]}

# (version, niveau) -> (codewords de correction par bloc, [(nb blocs, codewords de donnees), ...])
ECC = {
    (1, 'L'): (7, [(1, 19)]),   (1, 'M'): (10, [(1, 16)]),
    (1, 'Q'): (13, [(1, 13)]),  (1, 'H'): (17, [(1, 9)]),
    (2, 'L'): (10, [(1, 34)]),  (2, 'M'): (16, [(1, 28)]),
    (2, 'Q'): (22, [(1, 22)]),  (2, 'H'): (28, [(1, 16)]),
    (3, 'L'): (15, [(1, 55)]),  (3, 'M'): (26, [(1, 44)]),
    (3, 'Q'): (18, [(2, 17)]),  (3, 'H'): (22, [(2, 13)]),
    (4, 'L'): (20, [(1, 80)]),  (4, 'M'): (18, [(2, 32)]),
    (4, 'Q'): (26, [(2, 24)]),  (4, 'H'): (16, [(4, 9)]),
    (5, 'L'): (26, [(1, 108)]), (5, 'M'): (24, [(2, 43)]),
    (5, 'Q'): (18, [(2, 15), (2, 16)]), (5, 'H'): (22, [(2, 11), (2, 12)]),
    (6, 'L'): (18, [(2, 68)]),  (6, 'M'): (16, [(4, 27)]),
    (6, 'Q'): (24, [(4, 19)]),  (6, 'H'): (28, [(4, 15)]),
    (7, 'L'): (20, [(2, 78)]),  (7, 'M'): (18, [(4, 31)]),
    (7, 'Q'): (18, [(2, 14), (4, 15)]), (7, 'H'): (26, [(4, 13), (1, 14)]),
    (8, 'L'): (24, [(2, 97)]),  (8, 'M'): (22, [(2, 38), (2, 39)]),
    (8, 'Q'): (22, [(4, 18), (2, 19)]), (8, 'H'): (26, [(4, 14), (2, 15)]),
    (9, 'L'): (30, [(2, 116)]), (9, 'M'): (22, [(3, 36), (2, 37)]),
    (9, 'Q'): (20, [(4, 16), (4, 17)]), (9, 'H'): (24, [(4, 12), (4, 13)]),
    (10, 'L'): (18, [(2, 68), (2, 69)]), (10, 'M'): (26, [(4, 43), (1, 44)]),
    (10, 'Q'): (24, [(6, 19), (2, 20)]), (10, 'H'): (28, [(6, 15), (2, 16)]),
}

ECL_BITS = {'L': 1, 'M': 0, 'Q': 3, 'H': 2}

# --- arithmetique de Galois GF(256) ---------------------------------------

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _poly_mul(p, q):
    r = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        if a:
            for j, b in enumerate(q):
                if b:
                    r[i + j] ^= _gf_mul(a, b)
    return r


def _rs_generator(degree):
    """Polynome generateur (x-a^0)(x-a^1)... , coefficients du degre fort au faible."""
    g = [1]
    for i in range(degree):
        g = _poly_mul(g, [1, _EXP[i]])
    return g


def _rs_encode(data, ec_len):
    """Codewords de correction d'erreur pour un bloc de donnees."""
    gen = _rs_generator(ec_len)
    res = list(data) + [0] * ec_len
    for i in range(len(data)):
        coef = res[i]
        if coef:
            lc = _LOG[coef]
            for j in range(1, len(gen)):
                if gen[j]:
                    res[i + j] ^= _EXP[_LOG[gen[j]] + lc]
    return res[len(data):]


# --- flux binaire ----------------------------------------------------------

def _data_codewords(version, ecl):
    return sum(nb * dc for nb, dc in ECC[(version, ecl)][1])


def _pick_version(nbytes, ecl):
    for v in range(1, 11):
        count_bits = 8 if v < 10 else 16
        need = 4 + count_bits + 8 * nbytes
        if need <= _data_codewords(v, ecl) * 8:
            return v
    raise ValueError("contenu trop long pour une version <= 10 (%d octets)" % nbytes)


def _bitstream(payload, version, ecl):
    capacity = _data_codewords(version, ecl) * 8
    count_bits = 8 if version < 10 else 16
    bits = []

    def put(value, length):
        for k in range(length - 1, -1, -1):
            bits.append((value >> k) & 1)

    put(0b0100, 4)                 # mode octet
    put(len(payload), count_bits)
    for byte in payload:
        put(byte, 8)

    put(0, min(4, capacity - len(bits)))          # terminateur
    while len(bits) % 8:                          # alignement octet
        bits.append(0)

    codewords = [int(''.join(str(b) for b in bits[i:i + 8]), 2)
                 for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    i = 0
    while len(codewords) < capacity // 8:         # remplissage
        codewords.append(pad[i % 2])
        i += 1
    return codewords


def _interleave(codewords, version, ecl):
    ec_len, groups = ECC[(version, ecl)]
    blocks, pos = [], 0
    for nb, dc in groups:
        for _ in range(nb):
            chunk = codewords[pos:pos + dc]
            pos += dc
            blocks.append((chunk, _rs_encode(chunk, ec_len)))

    out = []
    for i in range(max(len(d) for d, _ in blocks)):
        for data, _ in blocks:
            if i < len(data):
                out.append(data[i])
    for i in range(ec_len):
        for _, ec in blocks:
            out.append(ec[i])
    return out


# --- construction de la matrice -------------------------------------------

class _Canvas:
    def __init__(self, version):
        self.version = version
        self.size = version * 4 + 17
        self.m = [[0] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x, y, dark):
        """x = colonne, y = ligne."""
        self.m[y][x] = 1 if dark else 0
        self.fixed[y][x] = True

    def draw_function_patterns(self):
        size = self.size
        for i in range(size):                     # motifs de synchronisation
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            self._finder(cx, cy)
        centers = ALIGN[self.version]
        last = len(centers) - 1
        for i, cy in enumerate(centers):
            for j, cx in enumerate(centers):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                self._alignment(cx, cy)
        self._reserve_format()
        if self.version >= 7:
            self._version_info()

    def _finder(self, cx, cy):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < self.size and 0 <= y < self.size:
                    d = max(abs(dx), abs(dy))
                    self.set_fn(x, y, d != 2 and d != 4)

    def _alignment(self, cx, cy):
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self.set_fn(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    def _format_cells(self):
        """Emplacements (colonne, ligne) des 15 bits de format, dans l'ordre, deux copies."""
        size = self.size
        first = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] \
            + [(14 - i, 8) for i in range(9, 15)]
        second = [(size - 1 - i, 8) for i in range(8)] \
            + [(8, size - 15 + i) for i in range(8, 15)]
        return first, second

    def _reserve_format(self):
        first, second = self._format_cells()
        for x, y in first + second:
            self.set_fn(x, y, False)
        self.set_fn(8, self.size - 8, True)       # module noir obligatoire

    def _version_info(self):
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = (self.version << 12) | rem
        for i in range(18):
            bit = (bits >> i) & 1
            a, b = self.size - 11 + i % 3, i // 3
            self.set_fn(a, b, bit)
            self.set_fn(b, a, bit)

    def draw_codewords(self, codewords):
        i = 0
        total = len(codewords) * 8
        right = self.size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(self.size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = (self.size - 1 - vert) if upward else vert
                    if not self.fixed[y][x] and i < total:
                        self.m[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        for y in range(self.size):
            for x in range(self.size):
                if self.fixed[y][x]:
                    continue
                if _MASKS[mask](x, y):
                    self.m[y][x] ^= 1

    def draw_format(self, ecl, mask):
        data = (ECL_BITS[ecl] << 3) | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((data << 10) | rem) ^ 0x5412
        first, second = self._format_cells()
        for i, (x, y) in enumerate(first):
            self.set_fn(x, y, (bits >> i) & 1)
        for i, (x, y) in enumerate(second):
            self.set_fn(x, y, (bits >> i) & 1)
        self.set_fn(8, self.size - 8, True)

    # --- score de penalite (choix du masque) ---
    def penalty(self):
        size, m = self.size, self.m
        score = 0
        for line in (m, list(zip(*m))):           # regles 1 et 3, lignes puis colonnes
            for row in line:
                run, prev = 0, None
                for cell in row:
                    if cell == prev:
                        run += 1
                        if run == 5:
                            score += 3
                        elif run > 5:
                            score += 1
                    else:
                        prev, run = cell, 1
                s = ''.join(str(c) for c in row)
                score += 40 * (s.count('10111010000') + s.count('00001011101'))
        for y in range(size - 1):                 # regle 2 : blocs 2x2
            for x in range(size - 1):
                v = m[y][x]
                if v == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(row) for row in m)         # regle 4 : equilibre noir/blanc
        ratio = dark * 100 // (size * size)
        score += 10 * (abs(ratio - 50) // 5)
        return score


_MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
    lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
]


def encode(text, ecl='M'):
    """Retourne la matrice QR (0 = clair, 1 = sombre) du texte donne."""
    payload = text.encode('utf-8')
    version = _pick_version(len(payload), ecl)
    codewords = _interleave(_bitstream(payload, version, ecl), version, ecl)

    best, best_score = None, None
    for mask in range(8):
        c = _Canvas(version)
        c.draw_function_patterns()
        c.draw_codewords(codewords)
        c.apply_mask(mask)
        c.draw_format(ecl, mask)
        s = c.penalty()
        if best_score is None or s < best_score:
            best, best_score = c, s
    return best.m


# --- rendus ---------------------------------------------------------------

def to_png(matrix, scale=8, quiet=4, fg=(0, 0, 0), bg=(255, 255, 255)):
    """Encode la matrice en PNG (RGB, sans filtre) et retourne les octets."""
    import struct
    import zlib

    n = len(matrix)
    side = (n + 2 * quiet) * scale
    fg_px, bg_px = bytes(fg), bytes(bg)

    raw = bytearray()
    quiet_row = b'\x00' + bg_px * side
    for _ in range(quiet * scale):
        raw += quiet_row
    for row in matrix:
        line = bytearray(bg_px * (quiet * scale))
        for cell in row:
            line += (fg_px if cell else bg_px) * scale
        line += bg_px * (quiet * scale)
        for _ in range(scale):
            raw += b'\x00' + line
    for _ in range(quiet * scale):
        raw += quiet_row

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', side, side, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 9))
            + chunk(b'IEND', b''))


def to_ansi(matrix, quiet=2):
    """Rend la matrice avec des blocs colores pour un terminal."""
    white, black, reset = '\033[47m  \033[0m', '\033[40m  \033[0m', ''
    n = len(matrix)
    lines = []
    blank = white * (n + 2 * quiet)
    for _ in range(quiet):
        lines.append(blank)
    for row in matrix:
        lines.append(white * quiet
                     + ''.join(black if c else white for c in row)
                     + white * quiet)
    for _ in range(quiet):
        lines.append(blank)
    return '\n'.join(lines) + reset
