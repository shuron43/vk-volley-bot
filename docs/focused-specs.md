# Mode: Focused Specs

Specifications are small executable interpretations of behavior at the product boundary. Protect the smallest outcome that matters to a user while leaving its implementation free to change.

## Shape

- Give each independently failing behavior one owning spec. Split behaviors that can fail independently; keep evidence together when separation would create meaningless fragments.
- Name a business spec for its actor context and action: `Reader requests Member-only source`.
- Every step uses `Then` once to state its user-visible or durable outcome: `the Reader is sent to Membership without losing the source destination`. The scenario and `Then` should make the behavior clear without the executable evidence beneath them.
- When clause-backed, follow the executable-interpretation rules in the companion guide `feature-clause-writing.md` (not included in this repository).
- Business specs have one request step. Multi-step tapes belong to infrastructure behavior such as runtime transaction or session boundaries.
- Prefer several focused specs over a page inventory and specify only current product pressure; follow [piecemeal growth](mode-piecemeal-growth.md).

## Evidence

`Given`, the request, and `Expect*` calls are the current evidence for the behavior named by the scenario and `Then`.

- Match from the expected side: describe only the promised part of an event or structured response. Ignore additional fields and unrelated emitted events so behavior may grow without breaking existing specs; preserve the order of events that are expected.
- Require an exact or empty result only when totality is the promise, such as no emitted events, exact source contents, or archive bytes.
- Use the fewest observations that prove stable identity, state, routes, permissions, downloads, or durable effects. For HTML, target shallow behavior-bearing IDs, states, and links from the companion guide `ui-design.md` (not included in this repository), not wrappers, nesting, styling, or incidental copy.
- Exercise direct protected URLs when authorization matters; hidden links do not prove access control.

## Review the spec

- Is the behavior observable at the product boundary, meaningful to a user, and not duplicated by another spec?
- When clause-backed, does the step follow the clause-linkage rules?
- Does `Then` explain why all the evidence belongs together?
- Does every expected field, event, selector, or exact value protect this interpretation?
- Would added fields, unrelated events, harmless copy or layout changes, and wholesale refactoring leave the spec intact while its behavior stays the same?
