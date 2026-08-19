/**
 * Runs the control plane's own `assertPortableManifest` against a manifest on stdin.
 *
 * A generated repository's manifest is what the control plane must accept before it can verify
 * anything in that repository. Checking the *result* document says nothing about the manifest —
 * the two are validated by different functions, and only one of them was being exercised.
 */
const src = process.env["ACP_SRC"];
if (!src) throw new Error("ACP_SRC is required");
const { assertPortableManifest } = await import(`${src}/contracts/manifest.ts`);
const raw = await new Response(process.stdin as unknown as ReadableStream).text();
const decision = assertPortableManifest(JSON.parse(raw));
console.log(JSON.stringify({
  allowed: decision.allowed,
  reasonCode: decision.reasonCode,
  issues: "evidence" in decision && decision.evidence && (decision.evidence as Record<string, unknown>)["issues"]
    ? (decision.evidence as { issues: Array<{ path: string; message: string }> }).issues.map((i) => i.path)
    : [],
}));
