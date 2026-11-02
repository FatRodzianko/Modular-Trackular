// Deliberately broken firmware, for tools/web_patcher_node.mjs: each corruption below is
// a mistake a builder could make, and web/verify.js must reject every one of them.
//
// corruptions({ out, spec, specUrl, stockImage, io }) -> [[what, image | ptfBytes]]
// `out` is patchFirmware's result for `spec`. Each entry is a changed copy: an image
// ({ base, image }) or, for the .ptf text itself, the file's bytes ({ ptfBytes }).
const u32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
const put32 = (b, o, v) => { b[o] = v & 0xff; b[o + 1] = (v >>> 8) & 0xff; b[o + 2] = (v >>> 16) & 0xff; b[o + 3] = v >>> 24; };
const hex = (s) => parseInt(s, 16);
const text = (b) => new TextDecoder().decode(b);

function blTarget(img, off, at) {                    // a Thumb-2 BL's target
  const hi = img[off] | (img[off + 1] << 8), lo = img[off + 2] | (img[off + 3] << 8);
  const s = (hi >> 10) & 1, j1 = (lo >> 13) & 1, j2 = (lo >> 11) & 1;
  let v = (s << 24) | ((~(j1 ^ s) & 1) << 23) | ((~(j2 ^ s) & 1) << 22) | ((hi & 0x3ff) << 12) | ((lo & 0x7ff) << 1);
  if (s) v -= 1 << 25;
  return at + 4 + v;
}

async function partFiles(spec, url, io) {
  const k = spec.prebuilt_rel ? ["prebuilt_sym", "prebuilt_rel"] : ["sym", "rel"];
  const get = async (key) => (spec[key] ? text(await io.fetch(new URL(spec[key], url).href)) : "");
  const syms = {}, rel = { link: 0, offsets: [], ram: [] };
  for (const line of (await get(k[0])).split(/\r?\n/)) {
    const f = line.trim().split(/\s+/);
    if (f.length === 3) syms[f[2]] = hex(f[0]);
  }
  for (const line of (await get(k[1])).split(/\r?\n/)) {
    const f = line.trim().split(/\s+/);
    if (f[0] === "link") rel.link = Number(f[1]);
    else if (f[0] === "ram") rel.ram.push(Number(f[1]));
    else if (/^\d/.test(f[0] ?? "")) rel.offsets.push(Number(f[0]));
  }
  return { spec, syms, rel };
}

export async function corruptions({ out, spec, specUrl, stockImage, io }) {
  const { base, image } = out, map = JSON.parse(out.map);
  const placed = map.patches;
  const specs = spec.include
    ? await Promise.all(spec.include.map(async (inc) => {
        const url = new URL(inc, specUrl).href;
        return partFiles(JSON.parse(text(await io.fetch(url))), url, io);
      }))
    : [await partFiles(spec, specUrl, io)];
  const at = (name) => hex(placed.find((p) => p.name === name).code[0]);
  const list = [];
  const change = (what, f) => {
    const img = Uint8Array.from(image);
    if (f(img) !== false) list.push([what, { built: { base, image: img } }]);
  };

  change("a stock byte away from every hook", (b) => { b[0x60100001 - base] ^= 1; });
  const site = specs.flatMap((s) => s.spec.branches ?? [])[0];
  if (site) change("a hooked BL put back to the stock target", (b) => {
    const o = hex(site.at) - base;
    b.set(stockImage.subarray(o, o + 4), o);
  });
  const last = placed[placed.length - 1], lastCode = hex(last.code[0]);
  change(`a byte in ${last.name}'s code`, (b) => { b[lastCode + 40 - base] ^= 0x80; });
  change("the first byte after the first part", (b) => {
    const e = hex(placed[0].code[1]) - base;
    if (e >= b.length) return false;               // the part ends the image
    b[e] ^= 0x5a;
  });
  const moved = specs.find((s) => s.rel.offsets.length);
  if (moved) change(`a relocated word in ${moved.spec.name} moved 16 bytes`, (b) => {
    const o = at(moved.spec.name) + moved.rel.offsets[0] - base;
    put32(b, o, u32(b, o) + 16);
  });
  const withRam = specs.find((s) => s.rel.ram.length && placed.find((p) => p.name === s.spec.name).ram);
  if (withRam) {
    const mine = placed.find((p) => p.name === withRam.spec.name).ram;
    const moveRam = (d) => (b) => {
      for (const off of withRam.rel.ram) {
        const o = at(withRam.spec.name) + off - base;
        put32(b, o, u32(b, o) + d);
      }
    };
    change(`${withRam.spec.name}'s RAM moved 1 MB up, out of its region`, moveRam(0x100000));
    const other = placed.find((p) => p.ram && p.name !== withRam.spec.name && p.ram.region === mine.region);
    if (other) change(`${withRam.spec.name}'s RAM moved onto ${other.name}'s`,
                      moveRam((hex(other.ram.start) - hex(mine.start)) >>> 0));
  }
  // a chain word holding the stock target where it should hold the hook before it
  const seen = new Set(), chained = [];
  for (const s of specs)
    for (const br of s.spec.branches ?? []) {
      if (br.next && seen.has(br.at)) chained.push([s, br]);
      seen.add(br.at);
    }
  if (chained.length) {
    const [s, br] = chained[0];
    change(`${s.spec.name}'s chain word (${br.next}) set to the stock target`, (b) => {
      const o = at(s.spec.name) + s.syms[br.next] - s.rel.link - base;
      put32(b, o, (blTarget(stockImage, hex(br.at) - base, hex(br.at)) | 1) >>> 0);
    });
  }
  const vw = specs.flatMap((s) => s.spec.words ?? []).find((w) => w.version) ?? (spec.words ?? []).find((w) => w.version);
  if (vw) change("the version text's last letter", (b) => {
    const t = u32(b, hex(vw.at) - base) - base;
    let n = t;
    while (b[n]) n++;
    b[n - 1] ^= 1;
  });
  list.push(["16 more 0xFF bytes at the end", { built: { base, image: Uint8Array.from([...image, ...new Uint8Array(16).fill(0xff)]) } }]);
  list.push(["the last 16 bytes cut off", { built: { base, image: image.slice(0, image.length - 16) } }]);
  change("the copy table's entry 3 a word longer", (b) => { const o = 0x600302c4 + 8 - base; put32(b, o, u32(b, o) + 4); });
  if (map.itcm_stubs) change("an ITCM stub's jump", (b) => { b[hex(map.itcm_stubs.flash) + 4 - base] ^= 2; });

  const lines = text(out.ptf).split("\r\n");
  let tail = lines.length;                         // a data record near the end
  while (--tail > 0 && !lines[tail].startsWith(":10"));
  tail -= 5;
  const edit = (what, f) => {
    const l = [...lines];
    f(l);
    list.push([what, { ptfBytes: new TextEncoder().encode(l.join("\r\n")) }]);
  };
  edit("a bad checksum", (l) => { l[100] = l[100].slice(0, -1) + (l[100].endsWith("0") ? "1" : "0"); });
  edit("lowercase hex", (l) => { l[200] = l[200].toLowerCase(); });
  if (tail > 0) edit("a tail record left out", (l) => { l.splice(tail, 1); });
  list.push(["LF line endings", { ptfBytes: new TextEncoder().encode(lines.join("\n")) }]);
  return list;
}
