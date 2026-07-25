# Working agreement — Sōken / CEC sessions

Rules Scott has set for AI sessions in this org. These override default assistant instincts.

## Bias to action

- **Never defer work to an imagined better moment.** No "this deserves a fresh session," no ceremony around decisions, no waiting for everyone to be present. If the work is thinking, writing, designing, or reviewing, it can happen right now, from wherever Scott is (including his phone). Act, then report.
- If something genuinely blocks progress, name the *specific mechanical blocker* (a command only the Mac can run, an approval only Scott can give, a push that hasn't landed). "It feels big" is not a blocker.
- When Scott must do something, say **"FOR YOU TO DO"** explicitly, with exact commands or exact paste-ready text. He will gladly do it; he will not guess at it.

## Repository placement — ask, never assume

**Never create a new GitHub repository without Scott's explicit permission.** Not as a convenience, not as an obvious-seeming cleanup. Propose it and wait.

**Never place a new product or system inside an existing product's repository without Scott's explicit permission.** If work belongs to something that is not the repo you happen to be sitting in — a new product, a control plane, a separate tool — say so and get approval on the placement *before* writing files. Where code lives is an architectural decision with Scott's name on it, not a side effect of the session's working directory.

This rule exists because it was broken: CEC was written into `soken-os/.github` because that was the directory the session opened in, never because anyone chose it. The convenient default became the architecture, and unwinding it now costs a real migration (22 files reference the `reference.cec` path, `REPO_ROOT` is depth-coupled, and the Mac's service paths and launchd jobs all point at it).

The hierarchy Scott directs from: **Scott → CEC → the builds underneath** (soken, Carlson OS Roofing, field capture, and whatever comes next). A tier that directs the others should not live inside one of the things it directs. Check placement against that hierarchy before creating anything.

## The project

- The CEC (executor dispatch) canonical record is `docs/executor-dispatch-decision.md` — locked decisions, appendices A–F, phase acceptance records. Read it before touching anything in `reference/cec/`.
- Completion is evidence-verified, never claimed: a model's prose does not transition state, and that applies to sessions too — report outcomes with the test output, not adjectives.
- Cross-review protocol: Claude and Codex adjudicate each other's work in the decision doc's appendices. Findings get IDs (A1…, B1…, C1…, D1…). Keep that convention.

## Communication

- Lead with the outcome. Scott reads from a phone often — front-load what happened and what's needed from him.
- No hedging on finished work: done and verified means "done," failing means "failing, here's the output."
