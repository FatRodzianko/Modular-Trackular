// The patcher, in the browser: a port of tools/ptf.py and tools/patch_ptf.py.
//
// It must produce exactly the bytes tools/patch_ptf.py produces from the same specs,
// so it follows the Python code step by step, with the same checks and refusals; see
// those files for why each step is the way it is. tools/test_web_patcher.py compares
// the two byte for byte. No DOM code here: the same file runs under Node for the tests.
//
// Addresses go up to 0x84000000, above 2^31, where JavaScript's bitwise operators turn
// negative, so every 32-bit result goes through ">>> 0" and words are read and written
// with DataView's Uint32 methods.

export const VERSION_PREFIX = "%d.%d.%d-";
export const VERSION_MAX = 3;
export const ALIGN = 16;

// Same as RAM_REGIONS in tools/patch_ptf.py (the harness checks they agree).
export const RAM_REGIONS = {
  ocram_tail:       [0x20205000, 0x20208000, ["startup"]],
  dtcm_above_heap:  [0x20051C70, 0x20053000, ["startup"]],
  sdram_top:        [0x83FF9BF0, 0x84000000, []],
  dtcm_after_audio: [0x200094C0, 0x20009800, ["startup"]],
  itcm_tail:        [0x0001FB40, 0x00020000, ["startup", "opt_in"]],
};
export const RAM_DEFAULT_ORDER = ["ocram_tail", "dtcm_above_heap", "sdram_top", "dtcm_after_audio"];

export class PatchError extends Error {}

function fail(msg) {
  throw new PatchError(msg);
}

const hx = (v) => "0x" + (v >>> 0).toString(16);
const hx10 = (v) => "0x" + (v >>> 0).toString(16).padStart(8, "0");
const alignUp = (v, a) => ((v + a - 1) & ~(a - 1)) >>> 0;
const getU32 = (b, off) => new DataView(b.buffer, b.byteOffset, b.byteLength).getUint32(off, true);
const setU32 = (b, off, v) => new DataView(b.buffer, b.byteOffset, b.byteLength).setUint32(off, v >>> 0, true);

function bytesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

function concat(chunks) {
  const out = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
  let o = 0;
  for (const c of chunks) {
    out.set(c, o);
    o += c.length;
  }
  return out;
}

const ff = (n) => new Uint8Array(Math.max(0, n)).fill(0xff);
const ascii = (s) => new TextEncoder().encode(s);
const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, "0"));
const toHex = (b) => Array.from(b, (x) => HEX[x]).join("");
const NIBBLE = new Int8Array(128).fill(-1);
for (let i = 0; i < 16; i++) NIBBLE["0123456789abcdef".charCodeAt(i)] = NIBBLE["0123456789ABCDEF".charCodeAt(i)] = i;
const SPACE = new Set([0x20, 0x09, 0x0a, 0x0d, 0x0b, 0x0c]);

// Python's bytes.fromhex: pairs of hex digits, ASCII whitespace allowed between pairs.
// Returns null where fromhex raises.
function fromHex(s) {
  const out = new Uint8Array(s.length >> 1);
  let n = 0;
  let i = 0;
  while (i < s.length) {
    const c = s.charCodeAt(i);
    if (SPACE.has(c)) {
      i++;
      continue;
    }
    const c2 = s.charCodeAt(i + 1);              // NaN past the end
    const hi = c < 128 ? NIBBLE[c] : -1;
    const lo = c2 < 128 ? NIBBLE[c2] : -1;
    if (hi < 0 || lo < 0) return null;
    out[n++] = (hi << 4) | lo;
    i += 2;
  }
  return out.slice(0, n);
}

// Python's int(s, 16) and int(s, 0), for the forms the specs and tables use.
function parseHex(s) {
  const m = /^\s*([+-]?)(?:0[xX])?([0-9a-fA-F]+)\s*$/.exec(String(s));
  if (!m) fail(`invalid hex number ${JSON.stringify(s)}`);
  const v = parseInt(m[2], 16);
  return m[1] === "-" ? -v : v;
}

function parseAuto(s) {
  const t = String(s).trim();
  let m;
  if ((m = /^([+-]?)0[xX]([0-9a-fA-F]+)$/.exec(t))) return (m[1] === "-" ? -1 : 1) * parseInt(m[2], 16);
  if ((m = /^([+-]?)(0|[1-9][0-9]*)$/.exec(t))) return (m[1] === "-" ? -1 : 1) * parseInt(m[2], 10);
  fail(`invalid number ${JSON.stringify(s)}`);
}

function splitLines(text) {
  // str.splitlines() after universal newlines, as far as a hex file can tell.
  const lines = text.split(/\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e]/);
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

// ---- tools/ptf.py ----------------------------------------------------------------

export function readPtf(bytes, name = "firmware") {
  for (const b of bytes) if (b > 0x7f) throw new PatchError(`${name}: not a text file`);
  const text = new TextDecoder("ascii").decode(bytes);   // ASCII only, checked above
  const records = [];
  let ext = 0;
  let start = null;
  const lines = splitLines(text);
  for (let n = 1; n <= lines.length; n++) {
    const line = lines[n - 1];
    if (!line) continue;
    const raw = fromHex(line.slice(1));
    if (raw === null || line[0] !== ":" || raw.length === 0 ||
        (raw.reduce((a, b) => a + b, 0) & 0xff) || raw[0] !== raw.length - 5) {
      fail(`${name}:${n}: bad record`);
    }
    const kind = raw[3];
    const addr16 = (raw[1] << 8) | raw[2];
    const payload = raw.slice(4, raw.length - 1);
    if (kind === 0) {
      records.push({ kind: 0, addr: ext + addr16, payload });
    } else if (kind === 4) {
      ext = payload.reduce((a, b) => a * 256 + b, 0) * 65536;
      records.push({ kind: 4, addr: addr16, payload });
    } else if (kind === 5) {
      start = payload.reduce((a, b) => a * 256 + b, 0);
      records.push({ kind: 5, addr: addr16, payload });
    } else if (kind === 1) {
      records.push({ kind: 1, addr: addr16, payload });
      break;
    } else {
      fail(`${name}:${n}: unsupported record type ${kind}`);
    }
  }
  const data = records.filter((r) => r.kind === 0);
  if (!data.length) fail(`${name}: no data records`);
  let base = Infinity, top = -Infinity;
  for (const r of data) {
    base = Math.min(base, r.addr);
    top = Math.max(top, r.addr + r.payload.length);
  }
  const image = ff(top - base);
  for (const r of data) image.set(r.payload, r.addr - base);
  return { records, base, image, start };
}

export function dataRanges(ptf) {
  const ranges = [];
  for (const r of ptf.records) {
    if (r.kind !== 0) continue;
    const s = r.addr, e = r.addr + r.payload.length;
    if (ranges.length && ranges[ranges.length - 1][1] === s) ranges[ranges.length - 1][1] = e;
    else ranges.push([s, e]);
  }
  return ranges;
}

function hexLine(count, addr16, kind, payload) {
  let sum = count + (addr16 >> 8) + (addr16 & 0xff) + kind;
  let s = ":" + HEX[count] + HEX[addr16 >> 8] + HEX[addr16 & 0xff] + HEX[kind];
  for (const b of payload) {
    sum += b;
    s += HEX[b];
  }
  return (s + HEX[-sum & 0xff]).toUpperCase();
}

export function encodePtf(ptf) {
  const out = [];
  for (const r of ptf.records) {
    if (r.kind === 0) {
      const off = r.addr - ptf.base;
      const payload = ptf.image.subarray(off, off + r.payload.length);
      out.push(hexLine(payload.length, r.addr & 0xffff, 0, payload));
    } else {
      out.push(hexLine(r.payload.length, r.addr, r.kind, r.payload));
    }
  }
  return out.join("\r\n") + "\r\n";
}

// ---- tools/patch_ptf.py ----------------------------------------------------------

export function thumbBl(at, target) {
  const off = ((target & ~1) >>> 0) - (at + 4);
  if (!(-(1 << 24) <= off && off < (1 << 24))) fail(`BL from ${hx(at)} to ${hx(target)} out of range`);
  const s = (off >> 24) & 1;
  const i1 = (off >> 23) & 1, i2 = (off >> 22) & 1;
  const j1 = (~i1 ^ s) & 1, j2 = (~i2 ^ s) & 1;
  const imm10 = (off >> 12) & 0x3ff, imm11 = (off >> 1) & 0x7ff;
  const hw1 = 0xf000 | (s << 10) | imm10;
  const hw2 = 0xd000 | (j1 << 13) | (j2 << 11) | imm11;
  return new Uint8Array([hw1 & 0xff, hw1 >> 8, hw2 & 0xff, hw2 >> 8]);
}

export function blTarget(image, base, at) {
  const o = at - base;
  const hw1 = image[o] | (image[o + 1] << 8), hw2 = image[o + 2] | (image[o + 3] << 8);
  const s = (hw1 >> 10) & 1;
  const j1 = (hw2 >> 13) & 1, j2 = (hw2 >> 11) & 1;
  const i1 = ~(j1 ^ s) & 1, i2 = ~(j2 ^ s) & 1;
  let off = (s << 24) | (i1 << 23) | (i2 << 22) | ((hw1 & 0x3ff) << 12) | ((hw2 & 0x7ff) << 1);
  if (s) off -= 1 << 25;
  return at + 4 + off;
}

export function loadSymbols(text) {
  const syms = {};
  for (const line of splitLines(text)) {
    const parts = line.split(/\s+/).filter(Boolean);
    if (parts.length === 3) syms[parts[2]] = parseHex(parts[0]);
  }
  return syms;
}

export function loadRel(text, path) {
  const rel = { link: null, size: null, offsets: [], ram_link: null, ram_size: 0, ram_offsets: [] };
  for (const line of splitLines(text)) {
    const parts = line.split(/\s+/).filter(Boolean);
    if (!parts.length) continue;
    if (["link", "size", "ram_link", "ram_size"].includes(parts[0])) rel[parts[0]] = parseAuto(parts[1]);
    else if (parts[0] === "ram") rel.ram_offsets.push(parseAuto(parts[1]));
    else rel.offsets.push(parseAuto(parts[0]));
  }
  if (rel.link === null || rel.size === null) fail(`${path}: not a relocation table`);
  return rel;
}

const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const decoder = new TextDecoder();

// The spec keys this port handles. patch_ptf.py may learn new ones (e.g. ITCM hooks);
// a spec using one is refused here until the port has it too.
const SPEC_KEYS = new Set([
  "name", "description", "firmware", "stock_image_sha256", "bin", "sym", "rel", "prebuilt_bin",
  "prebuilt_sym", "prebuilt_rel", "base", "branches", "words", "ram_startup", "ram_regions", "standalone",
  "include", "version", "version_word",
]);

function checkKeys(spec) {
  const unknown = Object.keys(spec).filter((k) => !SPEC_KEYS.has(k));
  if (unknown.length) fail(`${spec.name}: the web patcher doesn't support ${unknown.join(", ")} yet`);
}

// io.fetch(url) -> Promise<Uint8Array>; URLs resolve like paths (file: or http:).
async function loadPatch(spec, specUrl, inBundle, io, log) {
  checkKeys(spec);
  // A served site has no local build/ folder, so the prebuilt files are used whenever
  // the spec names them, as patch_ptf.py does when build/ doesn't exist.
  let keys = ["bin", "sym", "rel"];
  if (has(spec, "prebuilt_bin")) {
    keys = ["prebuilt_bin", "prebuilt_sym", "prebuilt_rel"];
    log(`using the prebuilt patch: ${spec.prebuilt_bin}`);
  }
  const file = (k) => (spec[k] == null ? null : new URL(spec[k], specUrl).href);
  const blob = await io.fetch(file(keys[0]));
  const syms = loadSymbols(decoder.decode(await io.fetch(file(keys[1]))));
  const pinned = has(spec, "base") ? parseHex(spec.base) : null;
  const rel = file(keys[2]) ? loadRel(decoder.decode(await io.fetch(file(keys[2]))), spec[keys[2]]) : null;
  if (rel === null && pinned === null) fail(`${spec.name}: needs a relocation table ("rel") or a fixed "base"`);
  if (rel && rel.size !== blob.length) fail(`${spec.name}: relocation table is for ${rel.size} bytes, the code is ${blob.length}`);
  if (inBundle && spec.standalone) fail(`${spec.name} is a standalone patch and can't go into a bundle`);
  const order = spec.ram_regions ?? RAM_DEFAULT_ORDER;
  for (const r of order) {
    if (!has(RAM_REGIONS, r)) fail(`${spec.name}: unknown RAM region ${JSON.stringify(r)} (known: ${Object.keys(RAM_REGIONS).join(", ")})`);
  }
  const words = (spec.words ?? []).filter((wd) => !(inBundle && wd.version));
  return {
    name: spec.name, pinned, link: rel ? rel.link : pinned,
    rel: rel ? rel.offsets : null, blob: Uint8Array.from(blob), syms,
    ram_link: rel ? rel.ram_link : null, ram_size: rel ? rel.ram_size : 0,
    ram_rel: rel ? rel.ram_offsets : [], ram_order: order,
    ram_startup: Boolean(spec.ram_startup),
    branches: spec.branches, words,
  };
}

function assignRam(parts) {
  const free = {};
  for (const [name, [start]] of Object.entries(RAM_REGIONS)) free[name] = start;
  for (const p of parts) {
    p.ram = null;
    if (!p.ram_size) continue;
    for (const name of p.ram_order) {
      const [, end, flags] = RAM_REGIONS[name];
      if (p.ram_startup && !flags.includes("startup")) continue;
      const base = alignUp(free[name], ALIGN);
      if (base + p.ram_size <= end) {
        p.ram = [base, name];
        free[name] = base + p.ram_size;
        break;
      }
    }
    if (p.ram === null) {
      const left = p.ram_order.map((n) => `${n} ${RAM_REGIONS[n][1] - free[n]} B`).join(", ");
      fail(`${p.name}: no free RAM region has ${p.ram_size} bytes left (${left})`);
    }
  }
}

function setNext(p, symbol, value) {
  if (!has(p.syms, symbol)) fail(`${p.name}: no symbol ${JSON.stringify(symbol)} for "next"`);
  const off = p.syms[symbol] - p.base;
  if ((off & 3) || !(0 <= off && off <= p.blob.length - 4) || getU32(p.blob, off) !== 0) {
    fail(`${p.name}: ${symbol} is not an aligned zero word in the patch code`);
  }
  if ((p.rel ?? []).includes(off) || p.ram_rel.includes(off)) fail(`${p.name}: ${symbol} is in the relocation table`);
  setU32(p.blob, off, value);
}

function place(p, base) {
  const delta = base - p.link;
  if (delta && p.rel === null) fail(`${p.name} is linked at ${hx(p.link)} and has no relocation table to move it`);
  const ramDelta = p.ram ? p.ram[0] - p.ram_link : 0;
  for (const [offs, d] of [[p.rel ?? [], delta], [p.ram_rel, ramDelta]]) {
    for (const off of offs) setU32(p.blob, off, getU32(p.blob, off) + d);
  }
  p.base = base;
  const syms = {};
  for (const [k, v] of Object.entries(p.syms)) syms[k] = v + delta;
  p.syms = syms;
}

function symbol(p, name) {
  if (!has(p.syms, name)) fail(`${p.name}: no symbol ${JSON.stringify(name)}`);
  return p.syms[name];
}

// Patch `stockBytes` (the .ptf file's bytes) with `spec` (a parsed patch.json; relative
// paths in it resolve against `specUrl`). Returns the new .ptf's bytes, its .map.json
// text and the log. Throws PatchError where patch_ptf.py refuses.
export async function patchFirmware({ stockBytes, spec, specUrl, io, outName, sha256, onLog }) {
  const log = onLog ?? (() => {});
  const ptf = readPtf(stockBytes, "stock firmware");

  const digest = await sha256(ptf.image);
  if (digest !== spec.stock_image_sha256) fail(`stock image hash ${digest} does not match ${spec.firmware}`);

  checkKeys(spec);
  let parts, version;
  if (has(spec, "include")) {
    parts = [];
    for (const inc of spec.include) {
      const iurl = new URL(inc, specUrl).href;
      const ispec = JSON.parse(decoder.decode(await io.fetch(iurl)));
      if (ispec.stock_image_sha256 !== spec.stock_image_sha256) fail(`${inc} is for another firmware`);
      parts.push(await loadPatch(ispec, iurl, true, io, log));
    }
    version = ascii(spec.version);
    if (!(0 < version.length && version.length <= VERSION_MAX)) {
      fail(`version text ${JSON.stringify(spec.version)}: 1 to ${VERSION_MAX} characters`);
    }
  } else {
    parts = [await loadPatch(spec, specUrl, false, io, log)];
    version = null;
  }

  assignRam(parts);

  const stockEnd = ptf.base + ptf.image.length;
  let end = stockEnd;
  for (const p of parts) {
    const base = p.pinned !== null ? p.pinned : alignUp(end, ALIGN);
    if (base < end) fail(`${p.name} at ${hx(base)} overlaps what comes before it (ends ${hx(end)})`);
    place(p, base);
    end = base + p.blob.length;
  }

  const stock = Uint8Array.from(ptf.image);
  const image = Uint8Array.from(ptf.image);
  const sites = new Map();

  const claim = (at, who, chained) => {
    if (sites.has(at)) {
      const [first, firstChained] = sites.get(at);
      if (!(firstChained && chained)) fail(`${hx(at)}: patched by both ${first} and ${who}, and not chained by both ("next")`);
    }
    sites.set(at, [who, chained]);
    return at - ptf.base;
  };

  for (const p of parts) {
    for (const br of p.branches) {
      const at = parseHex(br.at);
      const off = claim(at, p.name, has(br, "next"));
      const old = fromHex(br.old);
      const found = stock.slice(off, off + 4);
      if (!bytesEqual(found, old)) fail(`${hx(at)}: expected ${toHex(old)}, found ${toHex(found)}`);
      const before = blTarget(image, ptf.base, at);
      const to = symbol(p, br.to);
      const neu = thumbBl(at, to);
      image.set(neu, off);
      let chain = "";
      if (has(br, "next")) {
        setNext(p, br.next, (before | 1) >>> 0);
        chain = `, ${br.next} -> ${hx(before)}`;
      }
      log(`${hx(at)}: bl -> ${br.to} @ ${hx(to)}  (${toHex(old)} -> ${toHex(neu)}${chain})  ${br.note ?? ""}`);
    }
  }

  const words = [];
  for (const p of parts) {
    for (const wd of p.words) words.push([wd, (symbol(p, wd.to) | (wd.thumb ? 1 : 0)) >>> 0, p]);
  }
  let versionText = null;
  if (version !== null) {
    end = alignUp(end, 4);
    const vaddr = end;
    const text = concat([ascii(VERSION_PREFIX), version, new Uint8Array([0])]);
    versionText = [vaddr, text];
    end = vaddr + text.length;
    const vw = spec.version_word;
    words.push([{ at: vw.at, old: vw.old, to: "version text", note: `1.2.0-${spec.version}` }, vaddr, { name: spec.name }]);
  }

  for (const [wd, value, p] of words) {
    const at = parseHex(wd.at);
    const off = claim(at, p.name, has(wd, "next"));
    const old = fromHex(wd.old);
    const found = stock.slice(off, off + 4);
    if ((at & 3) || !bytesEqual(found, old)) fail(`${hx(at)}: expected aligned word ${toHex(old)}, found ${toHex(found)}`);
    const before = getU32(image, off);
    setU32(image, off, value);
    const neu = image.slice(off, off + 4);
    let chain = "";
    if (has(wd, "next")) {
      setNext(p, wd.next, before);
      chain = `, ${wd.next} -> ${hx(before)}`;
    }
    const shown = wd.thumb ? (value & ~1) >>> 0 : value;
    log(`${hx(at)}: word -> ${wd.to} @ ${hx(shown)}  (${toHex(old)} -> ${toHex(neu)}${chain})  ${wd.note ?? ""}`);
  }

  // The patches' code (with their chain words filled), after the stock image.
  const tailChunks = [];
  let tailLen = 0;
  end = stockEnd;
  for (const p of parts) {
    tailChunks.push(ff(p.base - end), p.blob);
    tailLen += Math.max(0, p.base - end) + p.blob.length;
    end = p.base + p.blob.length;
  }
  if (versionText !== null) {
    const [vaddr, text] = versionText;
    tailChunks.push(ff(vaddr - stockEnd - tailLen), text);
  }
  const tail = concat(tailChunks);

  // The tail as 16-byte data records (+ type 04 records when the upper half changes).
  const endRecords = ptf.records.filter((r) => r.kind === 5 || r.kind === 1);
  const records = ptf.records.filter((r) => r.kind !== 5 && r.kind !== 1);
  let upper = null;
  for (const r of records) {
    if (r.kind === 4) upper = r.payload.reduce((a, b) => a * 256 + b, 0);
  }
  const added = [];
  for (let i = 0; i < tail.length; i += 16) {
    const a = stockEnd + i;
    if (Math.floor(a / 65536) !== upper) {
      upper = Math.floor(a / 65536);
      added.push({ kind: 4, addr: 0, payload: new Uint8Array([upper >> 8, upper & 0xff]) });
    }
    added.push({ kind: 0, addr: a, payload: tail.slice(i, i + 16) });
  }
  const fullImage = concat([image, tail]);
  const patched = { records: [...records, ...added, ...endRecords], base: ptf.base, image: fullImage, start: ptf.start };
  const out = ascii(encodePtf(patched));

  const check = readPtf(out, outName);
  if (check.base !== ptf.base || !bytesEqual(check.image, fullImage) || check.start !== ptf.start) fail("read-back mismatch");
  if (dataRanges(check).length !== 1) fail(`output has gaps: ${JSON.stringify(dataRanges(check))}`);
  log(`wrote ${outName}: ${fullImage.length} bytes (${parts.map((p) => `${p.name} ${p.blob.length} bytes at ${hx(p.base)}`).join(", ")})`);
  for (const p of parts) {
    if (p.ram) log(`  RAM: ${p.name} ${p.ram_size} bytes at ${hx(p.ram[0])} (${p.ram[1]})`);
  }
  const layout = {
    output: outName,
    version_text: "1.2.0-" + (version !== null ? spec.version : "(the patch's own)"),
    patches: parts.map((p) => ({
      name: p.name,
      code: [hx10(p.base), hx10(p.base + p.blob.length)],
      ram: p.ram ? { region: p.ram[1], start: hx10(p.ram[0]), end: hx10(p.ram[0] + p.ram_size) } : null,
    })),
  };
  return { ptf: out, image: fullImage, base: ptf.base, map: JSON.stringify(layout, null, 2) + "\n" };
}

// The text Config shows: the format string the version word points to, with the
// stock "%d.%d.%d" filled in as 1.2.0 (what tools/emu/check_version.py prints).
export function versionTextOf(image, base, wordAt) {
  const ptr = getU32(image, wordAt - base);
  let s = "";
  for (let i = ptr - base; i >= 0 && i < image.length && image[i] !== 0; i++) s += String.fromCharCode(image[i]);
  return s.replace("%d.%d.%d", "1.2.0");
}

// ---- What the page builds for a selection ----------------------------------------

function relPath(from, to) {
  // Path of site file `to`, relative to the folder of site file `from`.
  const a = from.split("/").slice(0, -1), b = to.split("/");
  let i = 0;
  while (i < a.length && i < b.length - 1 && a[i] === b[i]) i++;
  return [...a.slice(i).map(() => ".."), ...b.slice(i)].join("/");
}

function resolveSite(from, rel) {
  // Site path of `rel` relative to the folder of site file `from`.
  const out = from.split("/").slice(0, -1);
  for (const part of rel.split("/")) {
    if (part === "..") out.pop();
    else if (part !== "." && part !== "") out.push(part);
  }
  return out.join("/");
}

// The mods patch/all bundles, as manifest ids.
export function allIds(manifest, allSpec) {
  return allSpec.include.map((inc) => {
    const path = resolveSite(manifest.all, inc);
    const entry = manifest.patches.find((p) => p.spec === path);
    if (!entry) throw new PatchError(`${manifest.all} includes ${inc}, which isn't in the manifest`);
    return entry.id;
  });
}

// The key of a selection in expected.json: its ids in the manifest's order.
export function selectionKey(manifest, ids) {
  return manifest.patches.filter((p) => ids.includes(p.id)).map((p) => p.id).join("+");
}

// Which spec a selection uses: one mod's own, patch/all's, or a bundle of the mods in
// the manifest's order with version text "M" + the selection's bits as two hex digits.
export function planSelection(manifest, allSpec, ids) {
  const chosen = manifest.patches.filter((p) => ids.includes(p.id));
  if (!chosen.length || chosen.length !== new Set(ids).size) throw new PatchError("unknown or empty selection");
  const key = chosen.map((p) => p.id).join("+");
  if (chosen.length === 1) return { kind: "single", key, specPath: chosen[0].spec, spec: null };
  const inAll = allIds(manifest, allSpec);
  if (inAll.length === chosen.length && chosen.every((p) => inAll.includes(p.id))) {
    return { kind: "all", key, specPath: manifest.all, spec: null };
  }
  const mask = chosen.reduce((m, p) => m | (1 << p.bit), 0);
  if (mask > 0xff) throw new PatchError("selection code needs more than two hex digits");
  const version = "M" + mask.toString(16).toUpperCase().padStart(2, "0");
  const names = chosen.map((p) => p.id);
  const spec = {
    name: `mix-${version}`,
    description: `Web patcher mix: ${names.join(" + ")}`,
    firmware: allSpec.firmware,
    stock_image_sha256: allSpec.stock_image_sha256,
    include: chosen.map((p) => relPath(manifest.all, p.spec)),
    version,
    version_word: allSpec.version_word,
  };
  return { kind: "mix", key, specPath: manifest.all, spec };
}

// Where the version word is for a spec: a bundle's version_word, or a mod's own
// "version": true word.
export function versionWordAt(spec) {
  if (spec.version_word) return parseHex(spec.version_word.at);
  const wd = (spec.words ?? []).find((w) => w.version);
  return wd ? parseHex(wd.at) : null;
}
