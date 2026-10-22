/** Read-only bridge to ACP's authoritative schemas. No writes, GitHub calls or daemon calls. */
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const src = process.env["ACP_SRC"];
if (!src) throw new Error("ACP_SRC is required");
const load = (path: string) => import(pathToFileURL(`${src}/${path}`).href);
const { projectManifestSchema, manifestDigest } = await load("contracts/manifest.ts");
const { digestOf } = await load("core/digest.ts");
const input = JSON.parse(readFileSync(0, "utf8"));
try {
  const manifest = projectManifestSchema.parse(input.projectManifest);
  const result: Record<string, unknown> = { manifest, manifestDigest: manifestDigest(manifest) };
  if (input.genesis) {
    // All v1 fields are explicit in RF's wire form. ACP may additionally normalize them.
    let genesis = input.genesis;
    const genesisPath = `${src}/bootstrap/genesis-bundle.ts`;
    // A missing new module means the deployed ACP has not yet gained genesis execution.
    // Do not hide parse failures or errors from a module that is present.
    const { existsSync } = await import("node:fs");
    if (existsSync(genesisPath)) {
      const module = await load("bootstrap/genesis-bundle.ts");
      if (!module.genesisBundleSchema) throw new Error("ACP genesisBundleSchema export is required");
      genesis = module.genesisBundleSchema.parse(genesis);
    }
    result.genesis = genesis;
    result.genesisDigest = digestOf(genesis);
  }
  console.log(JSON.stringify(result));
} catch {
  // Zod issues can contain rejected input; never echo credential-shaped input to stderr.
  console.log(JSON.stringify({error: "ACP_SCHEMA: manifest or genesis failed ACP's authoritative schema (VERIFICATION may be unsupported)"}));
  process.exitCode = 1;
}
