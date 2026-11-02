// Deterministic randomness, the same numbers as the desktop app (sequencer.rand and Python's random.Random),
// so a project plays and cuts the same on a phone as on a computer.

const MASK = (1n << 64n) - 1n;

function mix(x) {
  // splitmix64 finaliser.
  x = (x + 0x9E3779B97F4A7C15n) & MASK;
  x = ((x ^ (x >> 30n)) * 0xBF58476D1CE4E5B9n) & MASK;
  x = ((x ^ (x >> 27n)) * 0x94D049BB133111EBn) & MASK;
  return x ^ (x >> 31n);
}

// A uniform number in [0, 1) that depends only on the keys (whole numbers).
export function rand(...keys) {
  let h = 0n;
  for (const k of keys) h = mix(h ^ BigInt.asUintN(64, BigInt(Math.trunc(k))));
  return Number(h >> 11n) / 9007199254740992;
}

// Python's floor modulo: never negative for a positive divisor.
export function pmod(a, b) {
  return ((a % b) + b) % b;
}

// Python's round(): halves go to the even neighbour.
export function pyround(x) {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

// Python's random.Random (Mersenne Twister), for the seeded shard cuts.
export class PyRandom {
  constructor(seed) {
    this.mt = new Uint32Array(624);
    this.index = 624;
    let n = BigInt(Math.abs(Math.trunc(seed)));
    const key = [];
    do {
      key.push(Number(n & 0xffffffffn));
      n >>= 32n;
    } while (n > 0n);
    this._initByArray(key);
  }

  _init(s) {
    const mt = this.mt;
    mt[0] = s >>> 0;
    for (let i = 1; i < 624; i++) {
      const prev = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = (Math.imul(1812433253, prev) + i) >>> 0;
    }
    this.index = 624;
  }

  _initByArray(key) {
    const mt = this.mt;
    this._init(19650218);
    let i = 1, j = 0;
    for (let k = Math.max(624, key.length); k; k--) {
      const prev = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = ((mt[i] ^ Math.imul(prev, 1664525)) + key[j] + j) >>> 0;
      i++; j++;
      if (i >= 624) { mt[0] = mt[623]; i = 1; }
      if (j >= key.length) j = 0;
    }
    for (let k = 623; k; k--) {
      const prev = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = ((mt[i] ^ Math.imul(prev, 1566083941)) - i) >>> 0;
      i++;
      if (i >= 624) { mt[0] = mt[623]; i = 1; }
    }
    mt[0] = 0x80000000;
  }

  _uint32() {
    const mt = this.mt;
    if (this.index >= 624) {
      for (let k = 0; k < 624; k++) {
        const y = (mt[k] & 0x80000000) | (mt[(k + 1) % 624] & 0x7fffffff);
        mt[k] = mt[(k + 397) % 624] ^ (y >>> 1) ^ (y & 1 ? 0x9908b0df : 0);
      }
      this.index = 0;
    }
    let y = mt[this.index++];
    y ^= y >>> 11;
    y ^= (y << 7) & 0x9d2c5680;
    y ^= (y << 15) & 0xefc60000;
    y ^= y >>> 18;
    return y >>> 0;
  }

  random() {
    const a = this._uint32() >>> 5, b = this._uint32() >>> 6;
    return (a * 67108864 + b) / 9007199254740992;
  }

  uniform(a, b) {
    return a + (b - a) * this.random();
  }

  _below(n) {
    const k = Math.floor(Math.log2(n)) + 1;
    let r = this._uint32() >>> (32 - k);
    while (r >= n) r = this._uint32() >>> (32 - k);
    return r;
  }

  choice(seq) {
    return seq[this._below(seq.length)];
  }

  // random.choices(population, weights) with k=1.
  choices(population, weights) {
    const cum = [];
    let total = 0;
    for (const w of weights) cum.push(total += w);
    const x = this.random() * total;
    let lo = 0, hi = population.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (x < cum[mid]) hi = mid; else lo = mid + 1;
    }
    return population[lo];
  }
}
