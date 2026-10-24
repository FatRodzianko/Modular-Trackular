// The page: choose the stock firmware, choose mods, patch, download.
// The patching itself is web/patcher.js; this file only drives it.
import {
  PatchError, allIds, patchFirmware, planSelection, readPtf, versionTextOf, versionWordAt,
} from "./patcher.js";

const $ = (id) => document.getElementById(id);
const base = document.baseURI;
// The build this page and its code belong to: web/stage.py names it ("dev" unstaged).
const BUILD = "dev";
const cache = new Map();

async function fetchBytes(url) {
  if (!cache.has(url)) {
    cache.set(url, (async () => {
      // Revalidate, so a deploy never mixes old patch files with new expected hashes.
      const r = await fetch(url, { cache: "no-cache" });
      if (!r.ok) throw new Error(`couldn't load ${new URL(url).pathname} (${r.status})`);
      return new Uint8Array(await r.arrayBuffer());
    })());
  }
  return cache.get(url);
}
const fetchJson = async (path) => JSON.parse(new TextDecoder().decode(await fetchBytes(new URL(path, base).href)));
const io = { fetch: fetchBytes };

async function sha256(bytes) {
  const d = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, "0")).join("");
}

function show(el, text, kind) {
  el.hidden = false;
  el.className = "msg " + kind;
  el.textContent = text;
}

// ---- theme --------------------------------------------------------------------------

function setupTheme() {
  const btn = $("theme");
  // Light unless the viewer chose dark (the system setting doesn't switch it).
  const current = () => document.documentElement.dataset.theme || "light";
  const label = () => { btn.textContent = current() === "dark" ? "Light theme" : "Dark theme"; };
  try {
    const saved = localStorage.getItem("mt-theme");
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  } catch { /* storage blocked: light */ }
  label();
  btn.addEventListener("click", () => {
    const next = current() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("mt-theme", next); } catch { /* not remembered */ }
    label();
  });
}

// ---- state ----------------------------------------------------------------------------

const state = { manifest: null, allSpec: null, expected: null, stock: null, selected: new Set(), url: null };

function titles(ids) {
  return state.manifest.patches.filter((p) => ids.includes(p.id)).map((p) => p.title);
}

function renderGrid() {
  const grid = $("grid");
  for (const p of state.manifest.patches) {
    const card = document.createElement("label");
    card.className = "card";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = p.id;
    box.addEventListener("change", () => {
      if (box.checked) state.selected.add(p.id);
      else state.selected.delete(p.id);
      update();
    });
    const title = document.createElement("span");
    title.className = "card-title";
    title.textContent = p.title;
    const body = document.createElement("span");
    body.className = "card-body";
    body.textContent = p.summary;
    const foot = document.createElement("span");
    foot.className = "card-foot";
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = p.status;
    const more = document.createElement("a");
    more.href = `${state.manifest.repo}/blob/master/${p.spec.replace(/patch\.json$/, "README.md")}`;
    more.target = "_blank";
    more.rel = "noopener";
    more.textContent = "Details";
    foot.append(badge, more);
    card.append(box, title, body, foot);
    grid.append(card);
  }
  const inAll = allIds(state.manifest, state.allSpec);
  const everything = inAll.length === state.manifest.patches.length;
  $("select-all-label").textContent = everything
    ? `Select all (the “all” bundle, 1.2.0-${state.allSpec.version})`
    : `Select the “all” bundle: ${titles(inAll).join(", ")} (1.2.0-${state.allSpec.version})`;
  $("select-all").addEventListener("change", (e) => {
    state.selected = new Set(e.target.checked ? inAll : []);
    update();
  });
}

function update() {
  const ids = [...state.selected];
  for (const box of $("grid").querySelectorAll("input")) box.checked = state.selected.has(box.value);
  const inAll = allIds(state.manifest, state.allSpec);
  $("select-all").checked = ids.length === inAll.length && inAll.every((id) => state.selected.has(id));

  const sel = $("selection");
  sel.textContent = "";
  if (ids.length) {
    const plan = planSelection(state.manifest, state.allSpec, ids);
    const what = plan.kind === "single" ? `${titles(ids)[0]} on its own`
      : plan.kind === "all" ? `the “all” bundle, version text 1.2.0-${state.allSpec.version}`
      : `${titles(ids).join(" + ")}, version text 1.2.0-${plan.spec.version}`;
    const strong = document.createElement("strong");
    strong.textContent = what;
    sel.append("Selected: ", strong, ". ");
    // Whether the build this selection makes is one tested on hardware, by its hash.
    const tested = state.manifest.tested_builds[state.expected.selections[plan.key]];
    sel.append(tested
      ? "This is a hardware-tested build."
      : "Each mod is tested on hardware, but this combination hasn't been tested on hardware as one firmware. It passes the same layout checks as the tested builds.");
  } else {
    sel.textContent = "No mods selected.";
  }

  const ready = state.stock && ids.length;
  $("build").disabled = !ready;
  $("build-hint").hidden = Boolean(ready);
  clearResult();
}

function clearResult() {
  $("result").hidden = true;
  $("download").hidden = true;
  if (state.url) URL.revokeObjectURL(state.url);
  state.url = null;
}

// ---- step 1: the stock file ----------------------------------------------------------

async function takeFile(file) {
  const status = $("fw-status");
  state.stock = null;
  update();
  show(status, `Checking ${file.name}…`, "info");
  const bytes = new Uint8Array(await file.arrayBuffer());
  const want = state.manifest.stock;
  const digest = await sha256(bytes);
  if (digest === want.file_sha256) {
    const img = readPtf(bytes, file.name).image;
    if ((await sha256(img)) === want.image_sha256) {
      state.stock = bytes;
      show(status, `✓ ${file.name}: the stock Tracker+ 1.2.0 firmware (SHA-256 ${digest.slice(0, 12)}…). Ready.`, "ok");
      update();
      return;
    }
  }
  show(status, explain(bytes, file, digest, want), "bad");
}

function explain(bytes, file, digest, want) {
  const again = `Download ${want.file} again from Polyend's website and choose that file.`;
  let ptf;
  try {
    ptf = readPtf(bytes, file.name);
  } catch {
    return `${file.name} isn't a Tracker+ firmware file (.ptf). ${again}`;
  }
  // An already patched firmware: the version word points past the stock image.
  try {
    const stockEnd = 0x601bab78;
    if (ptf.base + ptf.image.length > stockEnd) {
      const text = versionTextOf(ptf.image, ptf.base, 0x600cbf9c);
      if (/^1\.2\.0-.{1,3}$/.test(text)) {
        return `${file.name} is already patched (it shows ${text} in Config). Start from the unmodified ${want.file}. ${again}`;
      }
    }
  } catch { /* not that */ }
  return `${file.name} is a firmware file, but not the unmodified Tracker+ 1.2.0 firmware (its SHA-256 is ` +
    `${digest.slice(0, 12)}…, the stock file's is ${want.file_sha256.slice(0, 12)}…). It may be another firmware ` +
    `version, another device's firmware, or a changed copy. ${again}`;
}

function setupDrop() {
  const drop = $("drop");
  $("file").addEventListener("change", (e) => {
    if (e.target.files[0]) takeFile(e.target.files[0]).catch(fatal);
  });
  for (const ev of ["dragenter", "dragover"]) {
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  }
  for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (f) takeFile(f).catch(fatal);
  });
}

// ---- step 3: patch -------------------------------------------------------------------

async function build() {
  const ids = [...state.selected];
  const status = $("result-status");
  const facts = $("result-facts");
  clearResult();
  $("result").hidden = false;
  facts.textContent = "";
  $("log").textContent = "";
  $("spec-box").hidden = true;
  show(status, "Patching…", "info");
  $("build").disabled = true;
  try {
    const plan = planSelection(state.manifest, state.allSpec, ids);
    const specUrl = new URL(plan.specPath, base).href;
    const spec = plan.spec ?? await fetchJson(plan.specPath);
    const lines = [];
    const out = await patchFirmware({
      stockBytes: state.stock, spec, specUrl, io, sha256,
      outName: "TrackerPlus.ptf", onLog: (l) => lines.push(l),
    });
    const at = versionWordAt(spec);
    const version = versionTextOf(out.image, out.base, at);
    const code = version.split("-")[1];
    const name = `TrackerPlus_${code}.ptf`;
    const digest = await sha256(out.ptf);
    lines.push(`file sha256 ${digest}`);
    $("log").textContent = lines.join("\n");

    const fact = (k, v) => {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      facts.append(dt, dd);
    };
    fact("File", name);
    fact("Mods", titles(ids).join(", "));
    fact("Config shows", `${version} Build 1504`);
    fact("Size", `${out.ptf.length.toLocaleString("en-US")} bytes`);
    fact("SHA-256", digest);

    const expected = state.expected.selections[plan.key];
    if (expected !== digest) {
      show(status, "✗ This output doesn't match the reference patcher's output for this selection, so there's " +
        "nothing to download. Reload the page and try again; if it happens again, please report it on GitHub.", "bad");
      return;
    }
    const tested = state.manifest.tested_builds[digest];
    show(status, tested
      ? `✓ Byte-identical to the hardware-tested build: ${tested}.`
      : "✓ Byte-identical to what the repository's Python patcher makes for this selection. This combination " +
        "hasn't been tested on hardware as one firmware: keep the stock file in /Firmware.", tested ? "ok" : "info");
    if (plan.spec) {
      $("spec").textContent = JSON.stringify(plan.spec, null, 2) + "\n";
      $("spec-box").hidden = false;
    }
    state.url = URL.createObjectURL(new Blob([out.ptf], { type: "application/octet-stream" }));
    const a = $("download");
    a.href = state.url;
    a.download = name;
    a.textContent = `Download ${name}`;
    a.hidden = false;
  } catch (e) {
    show(status, e instanceof PatchError ? `✗ The patcher refused: ${e.message}` : `✗ Something went wrong: ${e.message}`, "bad");
  } finally {
    $("build").disabled = !(state.stock && state.selected.size);
  }
}

// A browser may still hold an earlier build's page and code (GitHub Pages lets it keep
// them for 10 minutes) while the patch files, always revalidated, are the new build's:
// old code on new patch files refuses or mismatches. So when the site's build.json names
// another build, load the page again as ?build=<it>: an address not in the cache, whose
// page asks for that build's code. Returns false when the page is being replaced.
async function upToDate() {
  if (BUILD === "dev") return true;
  let live;
  try {
    const r = await fetch(new URL("build.json", base).href, { cache: "no-store" });
    live = r.ok ? (await r.json()).build : null;
  } catch {
    return true;                            // can't tell: carry on, as before this check
  }
  if (!live || live === BUILD) return true;
  const url = new URL(location.href);
  if (url.searchParams.get("build") === live) {
    throw new Error("the site is being updated and still sent this page from the earlier build (wait a minute)");
  }
  url.searchParams.set("build", live);
  location.replace(url.href);
  return false;
}

function fatal(e) {
  show($("load-error"), `The patcher couldn't start: ${e.message}. Reload the page to try again.`, "bad");
}

async function main() {
  setupTheme();
  if (!globalThis.crypto?.subtle) {
    throw new Error("this browser doesn't offer Web Crypto here (open the page over https)");
  }
  if (!(await upToDate())) return;
  [state.manifest, state.expected] = await Promise.all([fetchJson("manifest.json"), fetchJson("expected.json")]);
  state.allSpec = await fetchJson(state.manifest.all);
  renderGrid();
  setupDrop();
  $("build").addEventListener("click", () => build());
  update();
}

main().catch(fatal);
