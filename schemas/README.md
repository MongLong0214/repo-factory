# Schemas

Two of the contracts Repo Factory speaks are its own. The rest already have an implementation,
and the integration PRD §2.2 is explicit that they must not get a second one:

> 다음 Schema는 두 시스템이 공유하며 구현 정본은 한 곳만 둔다. … Repo Factory는 의미가
> 다른 복제 Schema를 만들면 안 된다.

## Defined here

| File | Contract | PRD |
|---|---|---|
| `bootstrap-request.schema.json` | What the control plane hands Repo Factory at Phase A | §13.1 |
| `bootstrap-plan.schema.json` | `repo-factory.bootstrap-plan.v2` — the exact approved intent | §8.1 |
| `bootstrap-profile.schema.json` | The shape of `profiles/*.json` | §6 |

## Defined in the control plane — deliberately not copied here

| Contract | Canonical implementation | Version |
|---|---|---|
| `ProjectManifest` | `src/contracts/manifest.ts` | `agent-control-plane.project.v2` |
| `RepoFactoryResult` | `src/bootstrap/repo-factory-result.ts` | `repo-factory.result.v2` |
| `ExternalWriteReceipt` | `src/bootstrap/repo-factory-result.ts` | — |
| `CandidateSnapshot` | `src/snapshot/candidate-snapshot.ts` | `agent-control-plane.candidate-snapshot.v1` |
| `VerificationCommand` | `src/verify/sandbox.ts` | — |
| `GitHubGateEvidence` | `src/github/github-kernel.ts`, **named `GatePayload`** | — |
| `ACPBootstrapActivationResult` | `src/bootstrap/activation.ts` | `agent-control-plane.bootstrap-activation.v1` |

Measured against `agent-control-plane@104ce7a`. Two of those version strings — `agent-control-plane.project.v2`
and `repo-factory.result.v2` — are the exact strings PRD §10.3 and §13.4 quote, so the contract
is already aligned rather than merely compatible.

**Why no local copy.** A vendored JSON Schema would be a second statement of the same contract,
and the two would agree only until one of them changed. The control plane parses
`RepoFactoryResult` and rejects a result that overclaims activation
(`BOOTSTRAP_RESULT_OVERCLAIMS_ACTIVATION`, ADR-0008), so the validator that matters already runs
on the receiving side. Repo Factory's job is to produce the shape, not to hold a second opinion
about it.

**`GitHubGateEvidence` is the trap.** The concept exists in the control plane under a different
name. Reading the PRD name alone makes it look absent, and defining it here fresh is exactly the
duplicate §2.2 forbids. It is recorded in this table rather than left to be rediscovered.

## Profiles are data, not code

`profiles/*.json` state what each bootstrap profile requires and permits. They validate against
`bootstrap-profile.schema.json`, which is what stops a profile from quietly growing a required
artifact nothing consumes — the failure §6.1 names when it says a document must not be produced
to satisfy a count.
