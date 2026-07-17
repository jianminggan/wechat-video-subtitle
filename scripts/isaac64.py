"""ISAAC64 key stream used by encrypted WeChat Channels video files.

Adapted from nobiyou/wx_channel under the MIT License. See THIRD_PARTY_NOTICES.md.
"""

MASK = (1 << 64) - 1
ENCRYPTED_PREFIX = 128 * 1024


class Isaac64:
    def __init__(self, seed: int):
        self.randrsl = [0] * 256
        self.mm = [0] * 256
        self.randcnt = 0
        self.aa = self.bb = self.cc = 0
        self.randrsl[0] = seed & MASK
        self._randinit()

    @staticmethod
    def _mix(a, b, c, d, e, f, g, h):
        a = (a - e) & MASK
        f = (f ^ (h >> 9)) & MASK
        h = (h + a) & MASK
        b = (b - f) & MASK
        g = (g ^ ((a << 9) & MASK)) & MASK
        a = (a + b) & MASK
        c = (c - g) & MASK
        h = (h ^ (b >> 23)) & MASK
        b = (b + c) & MASK
        d = (d - h) & MASK
        a = (a ^ ((c << 15) & MASK)) & MASK
        c = (c + d) & MASK
        e = (e - a) & MASK
        b = (b ^ (d >> 14)) & MASK
        d = (d + e) & MASK
        f = (f - b) & MASK
        c = (c ^ ((e << 20) & MASK)) & MASK
        e = (e + f) & MASK
        g = (g - c) & MASK
        d = (d ^ (f >> 17)) & MASK
        f = (f + g) & MASK
        h = (h - d) & MASK
        e = (e ^ ((g << 14) & MASK)) & MASK
        g = (g + h) & MASK
        return a, b, c, d, e, f, g, h

    def _randinit(self):
        values = [0x9E3779B97F4A7C13] * 8
        for _ in range(4):
            values = list(self._mix(*values))
        for base in range(0, 256, 8):
            values = [(values[i] + self.randrsl[base + i]) & MASK for i in range(8)]
            values = list(self._mix(*values))
            self.mm[base : base + 8] = values
        for base in range(0, 256, 8):
            values = [(values[i] + self.mm[base + i]) & MASK for i in range(8)]
            values = list(self._mix(*values))
            self.mm[base : base + 8] = values
        self._isaac64()
        self.randcnt = 256

    def _isaac64(self):
        self.cc = (self.cc + 1) & MASK
        self.bb = (self.bb + self.cc) & MASK
        for index in range(256):
            x = self.mm[index]
            branch = index & 3
            if branch == 0:
                self.aa = ~(self.aa ^ ((self.aa << 21) & MASK)) & MASK
            elif branch == 1:
                self.aa = (self.aa ^ (self.aa >> 5)) & MASK
            elif branch == 2:
                self.aa = (self.aa ^ ((self.aa << 12) & MASK)) & MASK
            else:
                self.aa = (self.aa ^ (self.aa >> 33)) & MASK
            self.aa = (self.aa + self.mm[(index + 128) & 255]) & MASK
            y = (self.mm[(x >> 3) & 255] + self.aa + self.bb) & MASK
            self.mm[index] = y
            self.bb = (self.mm[(y >> 11) & 255] + x) & MASK
            self.randrsl[index] = self.bb

    def generate(self, length: int) -> bytes:
        output = bytearray()
        while len(output) < length:
            if self.randcnt == 0:
                self._isaac64()
                self.randcnt = 256
            self.randcnt -= 1
            output.extend(self.randrsl[self.randcnt].to_bytes(8, "big"))
        return bytes(output[:length])


def decrypt_prefix(data: bytes, seed: int, offset: int = 0) -> bytes:
    """Decrypt bytes from a file range; only the first 128 KiB is encrypted."""
    if offset >= ENCRYPTED_PREFIX or not data:
        return data
    stream = Isaac64(seed).generate(min(ENCRYPTED_PREFIX, offset + len(data)))
    output = bytearray(data)
    count = min(len(output), ENCRYPTED_PREFIX - offset)
    for index in range(count):
        output[index] ^= stream[offset + index]
    return bytes(output)
