/** Read-only bridge to ACP's authoritative schemas. No writes, GitHub calls or daemon calls. */
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const src = process.env["ACP_SRC"];
if (!src) throw new Error("ACP_SRC is required");
const load = (path: string) => import(pathToFileURL(`${src}/${path}`).href);
const { projectManifestSchema, manifestDigest } = await load("contracts/manifest.ts");
const input = JSON.parse(readFileSync(0, "utf8"));
let reason = "manifest";
try {
  const manifest = projectManifestSchema.parse(input.projectManifest);
  const result: Record<string, unknown> = { manifest, manifestDigest: manifestDigest(manifest) };
  if (input.genesis) {
    reason = "genesis";
    // Mandatory: schema parsing alone omits byte, secret and manifest/CI checks.
    const { parseGenesis, genesisDigest } = await load("bootstrap/genesis-bundle.ts");
    const genesis = parseGenesis(input.genesis, manifest);
    result.genesis = genesis;
    result.genesisDigest = genesisDigest(genesis);
  }
  console.log(JSON.stringify(result));
} catch (error) {
  // Zod issues can contain rejected input; never echo credential-shaped input to stderr.
  // These are fixed categories, not arbitrary paths/messages taken from rejected input.
  if (error instanceof Error && error.message === "project id must be safe for resident allocation") {
    reason = "manifest.projectId";
  } else if (reason === "manifest") {
    const issues = (error as { issues?: { path?: unknown[] }[] })?.issues;
    const field = Array.isArray(issues) ? issues[0]?.path?.[0] : undefined;
    if (field === "projectId") reason = "manifest.projectId";
    if (field === "verificationCommands") reason = "manifest.verificationCommands";
    if (field === "ciWorkflows") reason = "manifest.ciWorkflows";
    if (field === "branchProfile") reason = "manifest.branchProfile";
  }
  console.log(JSON.stringify({error: "ACP_SCHEMA: manifest or genesis failed ACP's authoritative schema (VERIFICATION may be unsupported)", reason}));
  process.exitCode = 1;
}
