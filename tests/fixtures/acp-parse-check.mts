/**
 * Runs the control plane's own `parseRepoFactoryResult` against a result on stdin.
 *
 * The point is to check the produced shape against the authority rather than against a second
 * copy of its rules. `ACP_SRC` locates the checkout; the test skips when it is absent, because
 * an unrunnable check is not a passing one.
 */
const src = process.env["ACP_SRC"];
if (!src) throw new Error("ACP_SRC is required");
const { parseRepoFactoryResult } = await import(`${src}/bootstrap/repo-factory-result.ts`);
const raw = await new Response(process.stdin as unknown as ReadableStream).text();
const decision = parseRepoFactoryResult(JSON.parse(raw));
console.log(JSON.stringify({
  allowed: decision.allowed,
  reasonCode: decision.reasonCode,
  evidence: "evidence" in decision ? decision.evidence : null,
}));
