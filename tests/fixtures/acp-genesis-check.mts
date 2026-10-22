import { existsSync, readFileSync } from "node:fs";
const src = process.env["ACP_SRC"];
const { projectManifestSchema, manifestDigest } = await import(`${src}/contracts/manifest.ts`);
const { digestOf } = await import(`${src}/core/digest.ts`);
const { planForSubmission } = await import(`${src}/bootstrap/bootstrap-plan.ts`);
const { githubOperationSchema } = await import(`${src}/bootstrap/repo-factory-github.ts`);
const plan = JSON.parse(readFileSync(0, "utf8"));
const manifest = projectManifestSchema.parse(plan.projectManifest);
// Once ACP exports its genesis normalizer, use that rather than hashing the wire form.
let genesis = plan.genesis;
if (existsSync(`${src}/bootstrap/genesis-bundle.ts`)) {
  const module = await import(`${src}/bootstrap/genesis-bundle.ts`);
  genesis = module.genesisBundleSchema.parse(genesis);
}
console.log(JSON.stringify({manifest, manifestDigest: manifestDigest(manifest),
  genesisDigest: digestOf(genesis),
  operationsAllowed: plan.githubOperations.every((op: unknown) => githubOperationSchema.safeParse(op).success),
  submission: planForSubmission({kind: "PROJECT_BOOTSTRAP", projectId: null}, plan)}));
