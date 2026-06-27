# The One-Person $20M Builder: The Polsia Playbook Applied to a Residential Builder/Remodeler

*Strategy memo — Soken / Carlson Projects. Author: Scott Carlson. Date: 2026-06-27.*

> **Thesis:** A $20M residential building/remodeling company can be run by a tiny
> team — Scott + Stefan + Dawson + Tessa + a couple of superintendents — by using
> AI agents (Soken) to run the entire office/management layer, keeping humans only
> where judgment, relationships, and physical work actually require them.

---

## 1. Source: what the video showed

The reference is the video **"24h Inside a $30M Silicon Valley AI Startup with No
Employees"** — a day-in-the-life of **Polsia**, founded by **Ben Broca (Ben Cera)**.

- Raised **$30M** at a ~**$250M** valuation.
- **Zero employees** — one founder + a system of AI agents.
- Runs **~6,000–8,700 companies** on the platform.
- Crossed **$6–10M ARR**.

It is the cleanest live proof of a one-person company at real scale.

## 2. The Polsia playbook (the transferable ideas)

1. **The company is an overnight operating loop, not a tool.** Every night the
   agents wake up, read the state of the business (bugs, revenue, customers,
   pipeline), pick the single highest-leverage next action, *execute it*, and
   email the founder a morning report: what I did, what I'll do tomorrow, how the
   business is doing. The human reads it over coffee and steers.
2. **A CEO/strategist agent on top, specialist agents underneath.** One
   orchestrator (running the most capable, most expensive reasoning model — on
   purpose) sits above a task system that turns decisions into work handed to
   specialists: engineering, marketing, growth, ads, support, finance, research.
3. **Provision everything; never make the human wire things up.** Polsia spins up
   the email, server, database, Stripe, GitHub, ad account itself. Setup friction
   kills adoption.
4. **Build the end-state, not the staircase.** Don't ship a tool, then a tool,
   then stitch them. *"Integration creates value faster than optimization."*
5. **"80% AI, 20% taste."** The human's job collapses to judgment and direction
   — roughly **15 messages a day** to steer the whole company.
6. **Cross-company learning / network effect.** A learning from one company is
   anonymized into a shared knowledge base every company benefits from; errors
   caught once harden guardrails everywhere. **The system gets smarter as it runs.**
7. **Ruthless subtraction.** *"What are the 99 things you say no to and what do you
   double down on and polish."*
8. **Founder leverage at the edges.** Even at $10M ARR he still hands out his phone
   number. The human stays where relationship and taste live; the machine holds
   the operational middle.

## 3. Why this fits a GC even better than a software startup

A general contractor is **already an orchestration business.** The physical work is
done by subs/trades. What the builder actually sells is **coordination and
information**: estimating, bidding, scheduling, procurement, selections, RFIs,
change orders, billing, and client communication. That middle layer is exactly the
"operational middle" Polsia automated.

The usual objection — "construction is physical, you can't automate it" — cuts in
your favor: the physical part was **never your headcount** (it's the subs'). The part
Polsia proved you *can* automate is precisely the office layer that normally needs
15–30 staff at $20M.

---

## 4. The residential builder/remodeler version

Residential is different from commercial GC in ways that make the agent model
**stronger**, not weaker:

- **High volume of small decisions.** Remodels and custom homes generate hundreds
  of repetitive admin tasks (selections, allowances, scheduling, updates). High
  volume + repetition = exactly what agents are best at.
- **Homeowner clients are high-touch and anxious.** They want constant updates.
  The Polsia "nightly report" maps perfectly to an **automated weekly homeowner
  update** ("here's where your project is, what happened, what's next, any decisions
  we need from you"). Agents carry the *volume* of communication; humans own the
  *moments that matter* (design reveal, problem resolution).
- **Selections & allowances are where margin and conflict live.** This is the
  single biggest time sink and dispute source in residential. An agent that tracks
  every selection against its allowance, flags overages in real time, and auto-drafts
  the change order is enormous leverage.
- **Reputation is the growth engine.** Reviews, referrals, warranty/callbacks. Agents
  can run the entire follow-up and warranty-tracking loop that builders chronically
  drop.

### What the agent layer (Soken) runs

| Function | Today needs | Agent-run version |
|---|---|---|
| **Lead intake & qualification** | BD / admin | Agent qualifies inbound homeowners, scores fit/budget, books the consult — the remodeling top-of-funnel |
| **Estimating / takeoffs** | estimators | Quantity takeoff from plans; price against your historical cost-per-assembly DB; draft the proposal |
| **Selections & allowances** | selections coordinator | Track every selection vs. allowance, flag overages, auto-draft the CO, keep the running budget |
| **Proposals & contracts** | PM / owner | Generate proposal, scope, contract, payment schedule from the estimate |
| **Scheduling** | scheduler / PM | Maintain look-ahead, sequence trades, flag slips, notify subs and homeowner |
| **Sub/trade coordination** | PM | Send scopes & POs, confirm dates, chase confirmations, collect insurance/lien waivers |
| **Change orders** | PM | Price COs against the cost DB, draft, route for approval |
| **Draws / progress billing** | bookkeeper | Assemble draw requests, reconcile, track AR |
| **Homeowner communication** | PM | Automated weekly update + decision requests — the Polsia loop |
| **Punch list & warranty** | PM / super | Generate punch lists from walk-throughs, track callbacks, close them out |
| **Reviews & referrals** | nobody, usually | Post-completion follow-up, review requests, referral nurture |

### Where the 5 humans actually live (your "20% taste")

- **Scott** — deals, lot/land decisions, key client relationships, design taste,
  GO/NO-GO calls. The phone number.
- **Stefan / Dawson / Tessa** — the judgment layer: approving estimates and COs
  before they go out, owning the selections *conversations*, design decisions,
  exception-handling whatever the agents flag, the homeowner relationships that
  carry the brand.
- **Superintendents** — the irreducible physical layer: boots on site, trade
  coordination in the field, QA, safety, walk-throughs. You cannot agent away a
  body in the field, and you shouldn't try.

### Your unfair advantage: the "information theory project"

Polsia's moat is *cross-company* learning. Yours is **proprietary and private**:
every Carlson Projects job — actual costs, cost-per-assembly, selection prices, sub
performance and reliability, which leads converted and at what margin, where jobs
bled — is training data **no competitor has.** An estimating/selections/CO agent
pricing against *your real historical numbers* is something a generic AI can never
replicate. **Build the whole system around that dataset.** It is the moat.

---

## 5. The reality check (go in clear-eyed)

- **Polsia has no approval gates.** Fine when a bad email costs nothing; fatal when
  a mispriced bid, a wrong CO, or a sub commitment costs five or six figures. Your
  loop **must** keep a human-in-the-loop on anything touching money or contract:
  estimates out, COs, draws, sub commitments, contracts. That's not a weakness —
  it's *why you keep 5 people instead of 0.*
- **Residential is emotional.** The homeowner relationship matters *more* than in
  commercial. Agents absorb the admin volume; humans own the high-stakes human
  moments. Don't let the machine handle a furious homeowner.
- **Field risk is irreducible.** Safety, schedule, and quality live on site. Agents
  can *monitor and flag* but not *own* them — hence superintendents stay.
- **Build the end-state, not the staircase.** Don't build an estimating bot, then a
  scheduling bot. Pick **one full job's loop** and run it end-to-end with agents +
  your 5 people before scaling to the whole pipeline.

## 6. The 90-day plan

1. **Stand up the proprietary dataset first.** Cost-per-assembly, selection/allowance
   prices, sub performance, lead→close→margin history from Carlson's jobs. This is
   the foundation — do it before any agent.
2. **Build the estimating + selections agent** against that dataset. Highest
   leverage, most repetitive, clearest ROI, and it's where residential margin is
   won or lost.
3. **Stand up the homeowner-update loop** (the Polsia nightly email, weekly for
   residential) across active jobs — schedule status, decisions needed, selection
   overages, draws.
4. **Keep hard approval gates** on every money/contract action.
5. **Measure the one number that proves the thesis:** office-labor-hours per $1M of
   revenue removed. Drive it toward the level that makes $20M with 5 people real.

---

## Appendix: sources

- Polsia — https://polsia.com/
- The video — https://www.youtube.com/watch?v=OpsGJaijG10
- True Ventures, "The one-person company is no longer a metaphor" — https://www.trueventures.com/blog/polsia-one-person-company-no-longer-a-metaphor
- Henry Shi, "How a solo founder cloned himself" — https://henrythe9th.substack.com/p/how-a-solo-founder-cloned-himself
- Context Studios, "$1M ARR in 30 days" — https://www.contextstudios.ai/blog/polsia-how-a-solo-founder-hit-1m-arr-in-30-days-with-ai-agents
- GTMnow, "$30M at $250M with 0 employees" — https://gtmnow.com/gtm-192-inside-the-company-that-raised-30m-at-a-250m-valuation-with-0-employees-ben-cera-polsia/
- Agents at Work podcast w/ Ben — https://podcasttranscript.ai/library/agents-at-work-21-your-next-co-founder-is-an-ai
