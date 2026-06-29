# Handoff — Construction Entropy Study & AI Scaling Work

*Created: 2026-06-29 by a Claude Code **web/Cowork (cloud)** session for Scott Carlson.*
*Purpose: hand this task to a session that can reach the local Sōken docs (a local
Claude Code terminal session on Scott's Mac, or any session after the docs are on GitHub).*

---

## 1. The goal

Scott is developing the thesis that a **$20M residential builder/remodeler** can be
run by a tiny team (Scott + Stefan + Dawson + Tessa + a couple of superintendents)
using AI agents (**Sōken**) to run the entire office/management layer. The trigger
was the video *"24h Inside a $30M Silicon Valley AI Startup with No Employees"*
(Polsia / Ben Broca) — proof of a one-person company at scale.

The next piece Scott wants integrated is his **"study of construction entropy"** —
his information-theory framing of construction, summarized in his own QP Prep rock as:
> *"Build a process-oriented engine that reduces customer uncertainty (entropy),
> track weekly."*

This study is the conceptual backbone of Sōken (reduce customer/process entropy →
predictable, lean, agent-run delivery). It needs to be located, read, and woven into
the strategy work.

## 2. What's already done (this session)

- Researched the Polsia video and methodology (couldn't fetch the video/transcript
  directly — datacenter IP is blocked by YouTube + transcript sites — so synthesized
  from written/podcast coverage).
- Wrote and pushed a strategy memo: **`docs/ai-construction-scaling.md`** on branch
  **`claude/ai-construction-scaling-cybteo`** of `soken-os/.github`. It maps the
  Polsia playbook onto a residential builder/remodeler: the agent-run office layer,
  where the 5 humans stay, selections/allowances as the highest-leverage automation,
  and the proprietary cost dataset as the moat.

## 3. The blocker (why this is being handed off)

The **construction entropy study is NOT reachable from a cloud/Cowork session.**
Confirmed findings:

- The study lives in the **local "Sōken Main" docs repo on Scott's Mac**, backed up
  as `_backups/Soken-Docs-Backup-2026-06-29.zip` (49 MB, 461 files).
- Only the **manifest** was pushed to Google Drive
  (`Soken Docs Backups/Soken-Docs-Backup-2026-06-29.MANIFEST.txt`). The manifest
  states the 49 MB binary zip **cannot be pushed through the Drive connector** (it
  accepts inline content only).
- A full-text Drive search for "entropy" returns **only** the `6/17 QP Prep`
  spreadsheet (the rock quote above). The study itself is **not** in Drive.
- The cloud session runs in an isolated Linux VM with **no path to the Mac desktop
  or local filesystem** — there is no "request desktop access" capability. Cowork
  sessions are sandboxed from each other and from the physical machine.

The doc tree per the manifest (where the study almost certainly lives):
`00-Charter-and-Vision`, `01-Architecture`, `02-Modules`, `03-Integrations`,
`04-Data-Foundation`, `05-Sprints-and-Build-Log`, `06-Decisions-ADRs`,
`07-Pilot-Project`, `08-Operations-and-Infrastructure`, `09-Testing-and-QA`,
`10-Security-and-Compliance`, `11-User-Experience`, `12-Sales-and-Positioning`,
`13-Legal-and-IP`, `14-Glossary-and-Conventions`, plus `AGENTS.md`, `INDEX.md`,
and ~60 top-level `SOKEN-*.md/.docx/.xlsx/.html` docs.
**Most likely homes for the entropy study:** `00-Charter-and-Vision`,
`04-Data-Foundation`, or `14-Glossary-and-Conventions`.

## 4. What the next session should do

**If running locally (Claude Code in Scott's Mac terminal) — recommended:**
1. Locate the study: `grep -ril "entropy" ~/path/to/Soken-Main/` (also try
   "information theory", "uncertainty", "Shannon", "signal", "noise").
2. Read it. Reconcile it with the QP Prep rock and the strategy memo.
3. Integrate it into `docs/ai-construction-scaling.md` (or a new
   `docs/construction-entropy.md`) — make the entropy framing the explicit
   foundation of the agent-layer thesis.
4. Commit to branch `claude/ai-construction-scaling-cybteo` and push.

**To make this permanent (do this once):**
- Push the Sōken docs repo to GitHub under the `soken-os` org. Then *any* session —
  cloud or local — can read it. Tell the assisting agent the repo name so it can
  request access scope (this session was scoped to `soken-os/.github` only).

## 5. Key references

- Strategy memo: `soken-os/.github` → `docs/ai-construction-scaling.md`
  (branch `claude/ai-construction-scaling-cybteo`)
- QP Prep sheet (entropy rock): Google Drive file id
  `1Hv0pwsLO3g0zACuBd1o4-LIVnLWxdjs5IEv7AsTaUnk` ("6/17 QP Prep")
- Soken backup manifest: Drive file id `1z6_mZCNPsh3M17KWSrx0BYd1OgFTd6ve`
- Soken Cost Codes (example of a SOKEN doc that IS in Drive): Drive file id
  `1hYoC8kbWJpwOZPqGMQK_-ELGWp5bj52BWS-iVDCOxYI`
- The video: https://www.youtube.com/watch?v=OpsGJaijG10 (Polsia / Ben Broca)

## 6. The one-line ask for the next session

> Find Scott's construction-entropy / information-theory study in the local Sōken
> docs repo, read it, and integrate its framing into the AI-scaling strategy memo on
> the `claude/ai-construction-scaling-cybteo` branch. Then push the Sōken docs to
> `soken-os` on GitHub so this stops being a per-session problem.
