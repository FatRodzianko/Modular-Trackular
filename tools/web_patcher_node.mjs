// Runs the web patcher (web/patcher.js) under Node, on a staged site, for
// tools/test_web_patcher.py.
//
//   node tools/web_patcher_node.mjs <site dir> <stock.ptf> <jobs.json> <out dir>
//
// jobs.json is a list of {"name", "ids"} (a selection, planned as the page plans it)
// or {"name", "spec"} (a spec file, used as is). For each job it writes
// <out dir>/<name>.ptf and .map.json, and, for a mix, the spec it made to
// <site dir>/patch/<spec name>/patch.json, where tools/patch_ptf.py can read it.
// A selection job with "plan": true is only planned (its spec written, nothing patched).
// The stock firmware is parsed once, for all the jobs. Prints one JSON line per job.
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const P = await import(pathToFileURL(join(here, "..", "web", "patcher.js")).href);

const [siteDir, stockPath, jobsPath, outDir] = process.argv.slice(2).map((a) => resolve(a));
const site = pathToFileURL(siteDir + "/").href;
const io = { fetch: async (url) => new Uint8Array(readFileSync(fileURLToPath(url))) };
const sha256 = async (bytes) => createHash("sha256").update(bytes).digest("hex");
const stockBytes = new Uint8Array(readFileSync(stockPath));
let stock = null;                                    // parsed on the first job that patches
const manifest = JSON.parse(readFileSync(join(siteDir, "manifest.json"), "utf8"));
const allSpec = JSON.parse(readFileSync(join(siteDir, manifest.all), "utf8"));
mkdirSync(outDir, { recursive: true });

console.log(JSON.stringify({ constants: {
  VERSION_PREFIX: P.VERSION_PREFIX, VERSION_MAX: P.VERSION_MAX, ALIGN: P.ALIGN,
  RAM_REGIONS: P.RAM_REGIONS, RAM_DEFAULT_ORDER: P.RAM_DEFAULT_ORDER,
} }));

for (const job of JSON.parse(readFileSync(jobsPath, "utf8"))) {
  const result = { name: job.name };
  try {
    let spec, specUrl;
    if (job.ids) {
      const plan = P.planSelection(manifest, allSpec, job.ids);
      Object.assign(result, { kind: plan.kind, key: plan.key });
      if (plan.spec) {
        const path = `patch/${plan.spec.name}/patch.json`;
        mkdirSync(join(siteDir, "patch", plan.spec.name), { recursive: true });
        writeFileSync(join(siteDir, path), JSON.stringify(plan.spec, null, 2) + "\n");
        spec = plan.spec;
        specUrl = new URL(plan.specPath, site).href;   // includes resolve against patch/all/
        result.spec = path;
      } else {
        spec = JSON.parse(readFileSync(join(siteDir, plan.specPath), "utf8"));
        specUrl = new URL(plan.specPath, site).href;
        result.spec = plan.specPath;
      }
      if (job.plan) {
        console.log(JSON.stringify(Object.assign(result, { ok: true, planned: true })));
        continue;
      }
    } else {
      const path = resolve(siteDir, job.spec);
      spec = JSON.parse(readFileSync(path, "utf8"));
      specUrl = pathToFileURL(path).href;
      result.spec = job.spec;
    }
    const outName = `${job.name}.ptf`;
    stock ??= P.readPtf(stockBytes, "stock firmware");
    const out = await P.patchFirmware({ stockBytes, stock, spec, specUrl, io, outName, sha256 });
    writeFileSync(join(outDir, outName), out.ptf);
    writeFileSync(join(outDir, `${job.name}.map.json`), out.map);
    const at = P.versionWordAt(spec);
    Object.assign(result, {
      ok: true, sha256: await sha256(out.ptf),
      version_text: at === null ? null : P.versionTextOf(out.image, out.base, at),
    });
  } catch (e) {
    Object.assign(result, { ok: false, error: e instanceof P.PatchError ? e.message : String(e.stack ?? e) });
  }
  console.log(JSON.stringify(result));
}
