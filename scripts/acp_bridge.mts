/** Read-only bridge to ACP's authoritative schemas. No writes, GitHub calls or daemon calls. */
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const src = process.env["ACP_SRC"];
if (!src) throw new Error("ACP_SRC is required");
const load = (path: string) => import(pathToFileURL(`${src}/${path}`).href);
const { projectManifestSchema, manifestDigest } = await load("contracts/manifest.ts");
const input = JSON.parse(readFileSync(0, "utf8"));
try {
  const manifest = projectManifestSchema.parse(input.projectManifest);
  const result: Record<string, unknown> = { manifest, manifestDigest: manifestDigest(manifest) };
  if (input.genesis) {
    // Mandatory: schema parsing alone omits byte, secret and manifest/CI checks.
    const { parseGenesis, genesisDigest } = await load("bootstrap/genesis-bundle.ts");
    const genesis = parseGenesis(input.genesis, manifest);
    result.genesis = genesis;
    result.genesisDigest = genesisDigest(genesis);
  }
  console.log(JSON.stringify(result));
} catch {
  // Zod issues can contain rejected input; never echo credential-shaped input to stderr.
  console.log(JSON.stringify({error: "ACP_SCHEMA: manifest or genesis failed ACP's authoritative schema (VERIFICATION may be unsupported)"}));
  process.exitCode = 1;
}
