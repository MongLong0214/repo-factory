/** Read-only contract path: RF PLAN -> ACP submission -> producer input -> GitHub preflight. */
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const src = process.env["ACP_SRC"];
if (!src) throw new Error("ACP_SRC is required");
const load = (path: string) => import(pathToFileURL(`${src}/${path}`).href);
const { projectManifestSchema, manifestDigest } = await load("contracts/manifest.ts");
const { digestOf } = await load("core/digest.ts");
const { parseGenesis, genesisDigest } = await load("bootstrap/genesis-bundle.ts");
const { planForSubmission, plannedBootstrapOutputs, executablePlanOf } = await load("bootstrap/bootstrap-plan.ts");
const { repoFactoryPlanFixtureSchema } = await load("bootstrap/repo-factory-producer.ts");
const { genesisRepositoryOperations, preflightGitHubOperations } = await load("bootstrap/repo-factory-github.ts");
const { parseGitHubIdentity } = await load("bootstrap/github-write-port.ts");
const plan = JSON.parse(readFileSync(0, "utf8"));
const manifest = projectManifestSchema.parse(plan.projectManifest);
const result: Record<string, unknown> = { manifest, manifestDigest: manifestDigest(manifest) };
try {
  const genesis = parseGenesis(plan.genesis, manifest);
  result.normalization = { allowed: true, genesis };
  result.genesisDigest = genesisDigest(genesis);
} catch {
  // Never echo credential-shaped rejected bytes or Zod input in the test runner output.
  result.normalization = { allowed: false };
}
const submission = planForSubmission({ kind: "PROJECT_BOOTSTRAP", projectId: null }, plan);
result.submission = submission;
const identity = manifest.repositories[0].remote;
result.publicOperations = genesisRepositoryOperations(identity, manifest.ciWorkflows.map((w: {checkName: string}) => w.checkName));
const content = submission.allowed ? submission.value : plan;
const planDigest = digestOf(content);
if (submission.allowed) {
  const outputs = plannedBootstrapOutputs({ runId: "rf58-contract", planArtifact: { digest: planDigest, content } }, manifest);
  result.outputs = outputs;
  if (outputs.allowed) {
    result.producerSchemaAllowed = repoFactoryPlanFixtureSchema.safeParse(executablePlanOf(outputs.value, planDigest)).success;
  }
}
// Preflight executes ACP's expansion, never a Python reconstruction of its PR steps.
// Also run it on refused mutations so a missing PR step cannot be repaired by the bridge.
result.preflight = preflightGitHubOperations({ defaultBranch: manifest.branchProfile.defaultBranch, githubOperations: content.githubOperations }, {
  owner: parseGitHubIdentity(identity).owner,
  visibility: "public",
  approvedOperations: content.githubOperations.map(({ operationId, resourceType, resourceIdentity }: Record<string, string>) => ({ operationId, resourceType, resourceIdentity })),
});
console.log(JSON.stringify(result));
