// The page's check on what it built, before it offers it for download: reads the
// finished .ptf and accounts for every byte of its image, from the stock firmware and the
// patch files alone. Any byte that no rule below explains is a problem, and so is a RAM
// block outside the free regions or overlapping another.
//
// Written apart from web/patcher.js (the builder) and sharing none of its code: its own
// .ptf reader, its own Thumb-2 branch decoder, its own copy of the layout rules (the
// harness checks they equal tools/patch_ptf.py's). tools/test_web_patcher.py runs it on
// every selection the page offers, and on deliberately broken firmware it must reject.
// It is tools/test_layout.py's verify(), plus every stock byte, gap and padding byte.
//
// What it checks, for the parts (the bundle's included patches in order, or one patch):
//   - the .ptf: every line a valid record (CRLF, ":", hex, count, checksum); the stock
//     file's records in order with the same addresses and lengths, then 16-byte records
//     for what follows the stock image, then the stock's start and end records; one
//     unbroken range of data;
//   - each part right after the one before (16-byte aligned) or at its fixed base, with
//     0xFF before it; its bytes its .bin's, with each relocated word moved by the code
//     move or the RAM move (the RAM move read from the firmware: every RAM word must agree);
//   - its RAM block in a region it may use (startup-safe when it needs that, the ITCM
//     tail only when named), 16-byte aligned, inside the region; a late block likewise in
//     the default regions, its word holding its address; no two blocks overlapping, nor
//     the ITCM stubs;
//   - each chain word ("next") the stock target or the hook of the part before it on that
//     site; each link word the other part's symbol, or 0 without it;
//   - each hooked site a BL (or b.w for a tail call) to the last part's hook, or for a
//     word site the last part's value; every other stock byte unchanged;
//   - ITCM hooks: each site's BL to its stub, the stubs after the last part (0xFF gap),
//     moving the named registers and jumping to the last hook, copy table entry 3 copying
//     them to the ITCM tail (stock without ITCM hooks);
//   - a bundle's version text after them (4-byte aligned, 0xFF gap), the version word
//     pointing at it; then 0xFF padding exactly when the image would end in the first
//     256 bytes of a 4 KB flash sector, and nothing after.
//
// verifyFirmware({ ptfBytes | built, stockBytes | stock, spec, specUrl, io })
//   -> { ok, problems: [string], parts }
// `built` ({ image, base }, already parsed) and `stock` (parseStock's result) let the
// harness skip parsing text it has already checked; the page passes the bytes.

export const ALIGN = 16;
export const FLASH_SECTOR = 0x1000;
export const FLASH_PAGE = 0x100;
export const RAM_REGIONS = {
  ocram_tail: [0x20205000, 0x20208000, ["startup"]],
  dtcm_above_heap: [0x20051c70, 0x20053000, ["startup"]],
  sdram_top: [0x83ff9bf0, 0x84000000, []],
  dtcm_after_audio: [0x200094c0, 0x20009800, ["startup"]],
  itcm_tail: [0x0001fb40, 0x00020000, ["startup", "opt_in"]],
};
export const RAM_DEFAULT_ORDER = ["ocram_tail", "dtcm_above_heap", "sdram_top", "dtcm_after_audio"];
export const COPY_TABLE = 0x600302a0;
export const ITCM_ENTRY = 1;
export const STUB_ENTRY = 3;
export const STUB_ENTRY_STOCK = [0x601ad21c, 0x80000000, 0];
export const ITCM_STUBS = 0x1fb40;
export const REGS = { r4: 4, r5: 5, r6: 6, r7: 7, r8: 8, r9: 9, r10: 10, r11: 11, r12: 12, sp: 13 };
export const PREFIX_SHORT = "%d.%d.%d-";
export const PREFIX_LONG = "%d.%d-";           // with exactly 5 characters of version text

const u32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
const put32 = (b, o, v) => { b[o] = v & 0xff; b[o + 1] = (v >>> 8) & 0xff; b[o + 2] = (v >>> 16) & 0xff; b[o + 3] = v >>> 24; };
const h = (v) => "0x" + (v >>> 0).toString(16);
const up = (v, a) => Math.ceil(v / a) * a;
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

class Bad extends Error {}

// ---- the .ptf ---------------------------------------------------------------------

const HEXVAL = new Int8Array(128).fill(-1);
for (let i = 0; i < 10; i++) HEXVAL[48 + i] = i;
for (let i = 0; i < 6; i++) HEXVAL[65 + i] = 10 + i;

// Records of an Intel HEX file as this patcher writes it: CRLF after every line,
// capital hex digits. Throws Bad on anything else.
function readRecords(bytes) {
  const records = [];
  let i = 0, ext = 0, line = 0;
  while (i < bytes.length) {
    line++;
    if (bytes[i] !== 0x3a) throw new Bad(`line ${line}: doesn't start with ":"`);
    let j = i + 1;
    while (j < bytes.length && bytes[j] !== 0x0d) j++;
    if (bytes[j] !== 0x0d || bytes[j + 1] !== 0x0a) throw new Bad(`line ${line}: doesn't end with CRLF`);
    const n = j - i - 1;
    if (n < 10 || n % 2) throw new Bad(`line ${line}: odd or short hex`);
    const raw = new Uint8Array(n / 2);
    let sum = 0;
    for (let k = 0; k < raw.length; k++) {
      const a = HEXVAL[bytes[i + 1 + 2 * k]] ?? -1, b = HEXVAL[bytes[i + 2 + 2 * k]] ?? -1;
      if (a < 0 || b < 0) throw new Bad(`line ${line}: not capital hex`);
      raw[k] = (a << 4) | b;
      sum += raw[k];
    }
    if (sum & 0xff) throw new Bad(`line ${line}: bad checksum`);
    if (raw[0] !== raw.length - 5) throw new Bad(`line ${line}: byte count ${raw[0]} for ${raw.length - 5} bytes`);
    const kind = raw[3], addr16 = (raw[1] << 8) | raw[2], payload = raw.subarray(4, raw.length - 1);
    if (kind === 4) ext = ((payload[0] << 8) | payload[1]) * 65536;
    else if (kind !== 0 && kind !== 5 && kind !== 1) throw new Bad(`line ${line}: record type ${kind}`);
    records.push({ kind, addr: kind === 0 ? ext + addr16 : addr16, payload });
    i = j + 2;
    if (kind === 1) break;
  }
  if (i !== bytes.length) throw new Bad("data after the end record");
  return records;
}

// The image of data records that follow each other without a gap.
function imageOf(records) {
  const data = records.filter((r) => r.kind === 0);
  if (!data.length) throw new Bad("no data records");
  const base = data[0].addr;
  let end = base;
  for (const r of data) {
    if (r.addr !== end) throw new Bad(`data at ${h(r.addr)}, not right after ${h(end)}`);
    end += r.payload.length;
  }
  const image = new Uint8Array(end - base);
  for (const r of data) image.set(r.payload, r.addr - base);
  return { base, image };
}

// The stock .ptf, parsed once for many checks.
export function parseStock(stockBytes) {
  const records = readRecords(stockBytes);
  return { records, ...imageOf(records) };
}

// The output's records against the stock's and the tail's expected layout.
function checkRecords(records, stock, tailLength, problems) {
  const body = stock.records.filter((r) => r.kind !== 5 && r.kind !== 1);
  const ends = stock.records.filter((r) => r.kind === 5 || r.kind === 1);
  const want = body.map((r) => [r.kind, r.addr, r.payload.length, r.kind === 0 ? null : r.payload]);
  let upper = null;
  for (const r of body) if (r.kind === 4) upper = (r.payload[0] << 8) | r.payload[1];
  const stockEnd = stock.base + stock.image.length;
  for (let i = 0; i < tailLength; i += 16) {
    const a = stockEnd + i;
    if (Math.floor(a / 65536) !== upper) {
      upper = Math.floor(a / 65536);
      want.push([4, 0, 2, new Uint8Array([upper >> 8, upper & 0xff])]);
    }
    want.push([0, a, Math.min(16, tailLength - i), null]);
  }
  for (const r of ends) want.push([r.kind, r.addr, r.payload.length, r.payload]);
  const same = (r, w) => r.kind === w[0] && r.addr === w[1] && r.payload.length === w[2] &&
    (w[3] === null || r.payload.every((b, k) => b === w[3][k]));
  const at = want.findIndex((w, k) => !records[k] || !same(records[k], w));
  if (at >= 0 || records.length !== want.length) {
    problems.push(`the .ptf's records differ from the stock layout plus the patches' 16-byte records ` +
      `(record ${at >= 0 ? at + 1 : want.length + 1})`);
  }
}

// ---- Thumb-2 branches (decoded, not encoded) -----------------------------------------

// { form: "bl" | "b.w" | "", target } of the 4 bytes at `off` of `img` (address `at`).
function branchAt(img, off, at) {
  const hw1 = img[off] | (img[off + 1] << 8), hw2 = img[off + 2] | (img[off + 3] << 8);
  if ((hw1 & 0xf800) !== 0xf000) return { form: "", target: null };
  const form = (hw2 & 0xd000) === 0xd000 ? "bl" : (hw2 & 0xd000) === 0x9000 ? "b.w" : "";
  const s = (hw1 >> 10) & 1, j1 = (hw2 >> 13) & 1, j2 = (hw2 >> 11) & 1;
  const i1 = 1 - (j1 ^ s), i2 = 1 - (j2 ^ s);
  let disp = s * -(2 ** 24) + i1 * 2 ** 23 + i2 * 2 ** 22 + (hw1 & 0x3ff) * 4096 + (hw2 & 0x7ff) * 2;
  return { form, target: at + 4 + disp };
}

// ---- the patch files ---------------------------------------------------------------

const text = (b) => new TextDecoder().decode(b);
const num = (s) => (/^0x/i.test(s) ? parseInt(s.slice(2), 16) : parseInt(s, 10));

function symbolsOf(t) {
  const syms = {};
  for (const line of t.split(/\r?\n/)) {
    const f = line.trim().split(/\s+/);
    if (f.length === 3) syms[f[2]] = parseInt(f[0], 16);
  }
  return syms;
}

function relOf(t) {
  const rel = { link: null, size: null, offsets: [], ramLink: null, ramSize: 0, ramOffsets: [] };
  for (const line of t.split(/\r?\n/)) {
    const f = line.trim().split(/\s+/);
    if (!f[0]) continue;
    if (f[0] === "link") rel.link = num(f[1]);
    else if (f[0] === "size") rel.size = num(f[1]);
    else if (f[0] === "ram_link") rel.ramLink = num(f[1]);
    else if (f[0] === "ram_size") rel.ramSize = num(f[1]);
    else if (f[0] === "ram") rel.ramOffsets.push(num(f[1]));
    else rel.offsets.push(num(f[0]));
  }
  return rel;
}

async function loadPart(spec, url, inBundle, io) {
  const k = own(spec, "prebuilt_bin") ? ["prebuilt_bin", "prebuilt_sym", "prebuilt_rel"] : ["bin", "sym", "rel"];
  const file = async (key) => (spec[key] == null ? null : io.fetch(new URL(spec[key], url).href));
  const blob = await file(k[0]);
  const syms = symbolsOf(text(await file(k[1])));
  const relBytes = await file(k[2]);
  const rel = relBytes ? relOf(text(relBytes)) : null;
  return {
    name: spec.name, blob, syms, rel,
    pinned: own(spec, "base") ? parseInt(spec.base, 16) : null,
    branches: spec.branches ?? [],
    words: (spec.words ?? []).filter((w) => !(inBundle && w.version)),
    itcm: spec.itcm_branches ?? [],
    links: spec.links ?? [],
    lateWord: spec.late_ram ?? null,
    ramOrder: spec.ram_regions ?? RAM_DEFAULT_ORDER,
    ramStartup: Boolean(spec.ram_startup),
  };
}

// ---- the check ------------------------------------------------------------------------

export async function verifyFirmware({ ptfBytes, built, stockBytes, stock, spec, specUrl, io }) {
  const problems = [];
  try {
    stock ??= parseStock(stockBytes);
    let base, img, records = null;
    if (built) ({ base, image: img } = built);
    else {
      records = readRecords(ptfBytes);
      ({ base, image: img } = imageOf(records));
    }
    const sBase = stock.base, sImg = stock.image, stockEnd = sBase + sImg.length;
    if (base !== sBase) throw new Bad(`the image starts at ${h(base)}, not at the stock's ${h(sBase)}`);
    if (img.length < sImg.length) throw new Bad("the image is shorter than the stock image");

    const bundle = own(spec, "include");
    const parts = [];
    if (bundle) {
      for (const inc of spec.include) {
        const url = new URL(inc, specUrl).href;
        parts.push(await loadPart(JSON.parse(text(await io.fetch(url))), url, true, io));
      }
    } else {
      parts.push(await loadPart(spec, specUrl, false, io));
    }

    // What the image must be, built up from stock: each rule writes the bytes it explains.
    const want = new Uint8Array(img.length).fill(0xff);
    want.set(sImg);
    const claimed = new Map();                       // stock address -> who wrote it
    const claim = (at, n, who) => {
      for (let a = at; a < at + n; a++) {
        if (a < sBase || a >= stockEnd) throw new Bad(`${who}: ${h(at)} is outside the stock image`);
        if (claimed.has(a)) throw new Bad(`${who} at ${h(at)} overlaps ${claimed.get(a)}`);
      }
      for (let a = at; a < at + n; a++) claimed.set(a, who);
    };
    const blocks = [];                               // RAM blocks [start, end, what]
    const region = (start, end, allowed, startup, what) => {
      const fits = allowed.filter((r) => RAM_REGIONS[r][0] <= start && end <= RAM_REGIONS[r][1]);
      if (start % ALIGN) problems.push(`${what}: RAM at ${h(start)}, not 16-byte aligned`);
      if (!fits.length) problems.push(`${what}: RAM ${h(start)}..${h(end)} isn't inside a region it may use (${allowed.join(", ")})`);
      else if (startup && !RAM_REGIONS[fits[0]][2].includes("startup")) problems.push(`${what}: needs RAM usable at startup, got ${fits[0]}`);
      blocks.push([start, end, what]);
    };

    // The parts' code.
    let end = stockEnd;
    const placed = new Map();                        // name -> { part, at, syms (moved) }
    for (const p of parts) {
      if (p.rel === null && p.pinned === null) throw new Bad(`${p.name}: no relocation table and no fixed base`);
      if (p.rel && p.rel.size !== p.blob.length) throw new Bad(`${p.name}: relocation table for ${p.rel.size} bytes, code ${p.blob.length}`);
      const at = p.pinned !== null ? p.pinned : up(end, ALIGN);
      if (at < end) throw new Bad(`${p.name} at ${h(at)} overlaps what comes before it`);
      const link = p.rel ? p.rel.link : p.pinned;
      const move = at - link;
      if (move && !p.rel) throw new Bad(`${p.name}: linked at ${h(link)}, can't move`);
      if (at + p.blob.length - base > img.length) throw new Bad(`${p.name}: the image ends before its code does`);
      const syms = {};
      for (const [s, v] of Object.entries(p.syms)) syms[s] = v + move;
      placed.set(p.name, { p, at, syms, code: Uint8Array.from(p.blob), move });
      end = at + p.blob.length;
    }
    const sym = (q, s) => {
      if (!own(q.syms, s)) throw new Bad(`${q.p.name}: no symbol ${s}`);
      return q.syms[s];
    };
    const wordIn = (q, s) => {
      const off = sym(q, s) - q.at;
      if (off & 3 || off < 0 || off > q.code.length - 4) throw new Bad(`${q.p.name}: ${s} isn't an aligned word in its code`);
      return off;
    };

    // Relocation, RAM (its move read from the firmware), late RAM, links.
    for (const q of placed.values()) {
      const { p } = q;
      for (const off of p.rel ? p.rel.offsets : []) put32(q.code, off, u32(p.blob, off) + q.move);
      if (p.rel && p.rel.ramSize) {
        const moves = new Set(p.rel.ramOffsets.map((off) => (u32(img, q.at - base + off) - u32(p.blob, off)) >>> 0));
        if (moves.size > 1) problems.push(`${p.name}: its RAM words don't all move by the same amount`);
        if (moves.size) {
          const move = [...moves][0];
          for (const off of p.rel.ramOffsets) put32(q.code, off, u32(p.blob, off) + move);
          const start = (p.rel.ramLink + move) >>> 0;
          const allowed = p.ramOrder.filter((r) => own(RAM_REGIONS, r));
          region(start, start + p.rel.ramSize, allowed, p.ramStartup, `${p.name} RAM`);
        }
      }
      if (p.lateWord !== null) {
        const off = wordIn(q, p.lateWord);
        const size = u32(p.blob, off);
        const start = u32(img, q.at - base + off);
        put32(q.code, off, start);
        region(start, start + size, RAM_DEFAULT_ORDER, false, `${p.name} late RAM`);
      }
      for (const ln of p.links) {
        const other = placed.get(ln.patch);
        put32(q.code, wordIn(q, ln.word), other ? (sym(other, ln.symbol) | (ln.thumb ? 1 : 0)) >>> 0 : 0);
      }
    }

    // Hooks: flash branch and word sites, in the parts' order, each chained to the one before.
    const sites = new Map();                         // address -> { kind, value, form }
    const stockWord = (at) => u32(sImg, at - sBase);
    const stockBranch = (at) => branchAt(sImg, at - sBase, at);
    const stockIs = (at, old, who) => {           // the site's bytes in the stock firmware, as the spec says
      const hex = Array.from(sImg.subarray(at - sBase, at - sBase + 4), (b) => b.toString(16).padStart(2, "0")).join("");
      if (hex !== String(old).toLowerCase()) problems.push(`${who}: stock bytes ${hex}, the spec says ${old}`);
    };
    for (const q of placed.values()) {
      const { p } = q;
      for (const br of p.branches) {
        const at = parseInt(br.at, 16);
        const was = sites.get(at);
        if (!was) claim(at, 4, `${p.name} site ${h(at)}`);
        else if (was.kind !== "bl") throw new Bad(`${h(at)}: a branch and a word site`);
        stockIs(at, br.old, `${p.name} site ${h(at)}`);
        const before = was ? was.value : stockBranch(at).target;
        if (own(br, "next")) put32(q.code, wordIn(q, br.next), (before | 1) >>> 0);
        sites.set(at, { kind: "bl", value: sym(q, br.to), form: br.tail ? "b.w" : "bl" });
      }
      for (const wd of p.words) {
        const at = parseInt(wd.at, 16);
        const was = sites.get(at);
        if (!was) claim(at, 4, `${p.name} word ${h(at)}`);
        else if (was.kind !== "word") throw new Bad(`${h(at)}: a branch and a word site`);
        stockIs(at, wd.old, `${p.name} word ${h(at)}`);
        const before = was ? was.value : stockWord(at);
        if (own(wd, "next")) put32(q.code, wordIn(q, wd.next), before);
        sites.set(at, { kind: "word", value: (sym(q, wd.to) | (wd.thumb ? 1 : 0)) >>> 0 });
      }
    }

    // ITCM hooks: a stub per site after the last part, the site's BL (in the ITCM image in
    // flash) to its stub, chain words to the stock target or the hook before.
    const itcmLoad = u32(sImg, COPY_TABLE + 12 * ITCM_ENTRY - sBase);
    const itcmExec = u32(sImg, COPY_TABLE + 12 * ITCM_ENTRY + 4 - sBase);
    const itcm = new Map();                          // ITCM site -> { hook, args }
    for (const q of placed.values()) {
      for (const br of q.p.itcm) {
        const at = parseInt(br.at, 16), flash = itcmLoad + at - itcmExec;
        const args = br.args ?? [];
        const was = itcm.get(at);
        if (was && was.args.join() !== args.join()) throw new Bad(`ITCM ${h(at)}: different registers passed`);
        if (!was) claim(flash, 4, `${q.p.name} ITCM ${h(at)}`);
        stockIs(flash, br.old, `${q.p.name} ITCM ${h(at)}`);
        const before = was ? was.hook : stockBranch(flash).target - flash + at;
        if (own(br, "next")) put32(q.code, wordIn(q, br.next), (before | 1) >>> 0);
        itcm.set(at, { hook: sym(q, br.to), args });
      }
    }
    for (const q of placed.values()) want.set(q.code, q.at - base);

    // Hooked sites: each must decode to the last hook (and keep its form).
    for (const [at, s] of sites) {
      const off = at - base;
      if (s.kind === "word") {
        put32(want, off, s.value);
        continue;
      }
      const got = branchAt(img, off, at);
      if (got.form !== s.form || got.target !== (s.value & ~1)) {
        problems.push(`${h(at)}: a ${got.form || "non-branch"} to ${h(got.target ?? 0)}, not a ${s.form} to the hook at ${h(s.value & ~1)}`);
      }
      want.set(img.subarray(off, off + 4), off);     // explained by the decode above
    }
    if (itcm.size) {
      const stubs = [];
      let flashAt = up(end, ALIGN), itcmAt = ITCM_STUBS;
      for (const [at, { hook, args }] of itcm) {
        const flash = itcmLoad + at - itcmExec;
        const got = branchAt(img, flash - base, flash);
        if (got.form !== "bl" || got.target - flash + at !== itcmAt) {
          problems.push(`ITCM ${h(at)}: doesn't BL to its stub at ${h(itcmAt)}`);
        }
        want.set(img.subarray(flash - base, flash - base + 4), flash - base);
        const moves = args.map((r, i) => 0x4600 | (REGS[r] << 3) | (2 + i));
        while (args.length && moves.length < 2) moves.push(0xbf00);
        for (const m of moves) stubs.push(m & 0xff, m >> 8);
        stubs.push(0xdf, 0xf8, 0x00, 0xf0, ...[0, 8, 16, 24].map((s) => ((hook | 1) >>> s) & 0xff));
        itcmAt = ITCM_STUBS + stubs.length;
      }
      if (flashAt + stubs.length - base > img.length) throw new Bad("the image ends before the ITCM stubs do");
      want.set(stubs, flashAt - base);
      claim(COPY_TABLE + 12 * STUB_ENTRY, 12, "copy table entry 3");
      [flashAt, ITCM_STUBS, stubs.length].forEach((v, i) => put32(want, COPY_TABLE + 12 * STUB_ENTRY + 4 * i - base, v));
      if (ITCM_STUBS + stubs.length > RAM_REGIONS.itcm_tail[1]) problems.push("the ITCM stubs don't fit in the ITCM tail");
      blocks.push([ITCM_STUBS, ITCM_STUBS + stubs.length, "ITCM stubs"]);
      end = flashAt + stubs.length;
    } else {
      const entry = [0, 4, 8].map((o) => u32(sImg, COPY_TABLE + 12 * STUB_ENTRY + o - sBase));
      if (entry.join() !== STUB_ENTRY_STOCK.join()) problems.push("the stock copy table entry 3 isn't the unused one");
    }

    // A bundle's version text, its word pointing at it.
    if (bundle) {
      const v = String(spec.version);
      if (!/^[0-9A-Za-z]{1,3}$|^[0-9A-Za-z]{5}$/.test(v)) problems.push(`version text ${JSON.stringify(v)}`);
      const textAt = up(end, 4);
      const bytes = new TextEncoder().encode((v.length === 5 ? PREFIX_LONG : PREFIX_SHORT) + v + "\0");
      if (textAt + bytes.length - base > img.length) throw new Bad("the image ends before the version text does");
      want.set(bytes, textAt - base);
      const at = parseInt(spec.version_word.at, 16);
      stockIs(at, spec.version_word.old, "version word");
      claim(at, 4, "version word");
      put32(want, at - base, textAt);
      end = textAt + bytes.length;
    }

    // Padding: only when the image would end in the first 256 bytes of a flash sector.
    let finish = end;
    if (finish % FLASH_SECTOR > 0 && finish % FLASH_SECTOR <= FLASH_PAGE) {
      finish = finish - (finish % FLASH_SECTOR) + FLASH_PAGE + ALIGN;
    }
    if (base + img.length !== finish) problems.push(`the image ends at ${h(base + img.length)}, not at ${h(finish)}`);
    if (records) checkRecords(records, stock, img.length - sImg.length, problems);

    // No two RAM blocks overlap.
    blocks.sort((a, b) => a[0] - b[0]);
    for (let i = 1; i < blocks.length; i++) {
      if (blocks[i][0] < blocks[i - 1][1]) problems.push(`${blocks[i - 1][2]} and ${blocks[i][2]} overlap`);
    }

    // Every byte.
    const n = Math.min(want.length, img.length);
    let shown = 0, differing = 0;
    for (let i = 0; i < n; i++) {
      if (want[i] === img[i]) continue;
      differing++;
      if (shown++ < 5) {
        const a = base + i;
        const where = a < stockEnd ? (claimed.get(a) ?? "stock") :
          [...placed.values()].find((q) => q.at <= a && a < q.at + q.code.length)?.p.name ?? "after the parts";
        problems.push(`${h(a)} (${where}): ${img[i].toString(16).padStart(2, "0")}, should be ${want[i].toString(16).padStart(2, "0")}`);
      }
    }
    if (differing > shown) problems.push(`${differing} bytes differ in all`);
    return { ok: problems.length === 0, problems, parts: parts.map((p) => p.name) };
  } catch (e) {
    if (!(e instanceof Bad)) throw e;
    problems.push(e.message);
    return { ok: false, problems, parts: [] };
  }
}
