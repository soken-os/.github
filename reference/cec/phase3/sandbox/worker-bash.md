# Worker Bash confinement (P4 / finding E4)

## What this is

The CEC worker is a headless model CLI (`claude -p ...`) that runs each `Bash`
tool call in a shell it spawns. This control confines that shell's filesystem
**writes to the task worktree**, retiring the blanket-`bypassPermissions` caveat
(finding E4): the worker may still edit freely, but it can no longer write
anywhere outside the worktree it was dispatched into.

It is deliberately **not** a general security sandbox. See *Scope and limits*.

## Chosen mechanism: macOS `sandbox-exec` profile

The packet named two candidates and asked for the *smallest enforceable* one:
allowed-tool patterns if they enforceably restrict Bash, otherwise a macOS
`sandbox-exec` profile.

**Allowed-tool patterns do not enforceably restrict Bash, so they were
rejected.** Two independent reasons:

1. Claude Code's `--allowedTools` gates *permission prompting*, not execution.
   The project's own docs state permission rules are not a security boundary.
   Bash patterns are prefix matches over a command string; a command can trivially
   evade them (`cd /elsewhere && ...`, absolute paths, `bash -c '...'`, `env`,
   here-docs), so no finite allow-list confines *where a shell writes*.
2. This packet runs under `permission_mode: bypassPermissions` -- the mode E4
   exists to make safe. In that mode allowed-tool patterns are waived entirely,
   so they restrict nothing at all.

The enforceable control is therefore a **`sandbox-exec` profile**
(`worker.sb`). It is applied at process launch by
`ClaudeCodeAdapter._argv` (`reference/cec/adapters.py`), which prefixes the CLI
argv with:

```
/usr/bin/sandbox-exec -f <...>/worker.sb \
    -D WORKTREE_ROOT=<realpath of the task worktree> \
    -D HOME_STATE=<realpath of the CLI's state dir, e.g. ~/.claude> \
    -D PROC_TMP=<realpath of $TMPDIR> \
    claude -p <objective> ...
```

The kernel sandbox is **inherited by every child process**, so wrapping the CLI
also binds every shell the CLI spawns for a `Bash` call -- which is exactly the
surface E4 is about, not just the CLI itself.

### The single enforced rule

`worker.sb` is `(allow default)` with one carved-out boundary:

```scheme
(deny file-write*)
(allow file-write* (subpath (param "WORKTREE_ROOT")))
(allow file-write* (subpath (param "HOME_STATE")))
(allow file-write* (subpath (param "PROC_TMP")))
```

In SBPL the **last matching rule wins**: a path inside the worktree is denied by
the blanket rule and then re-allowed; a path anywhere else keeps the deny.
Reads, process execution, and network are left at `allow default` on purpose, so
a normal build/test turn behaves exactly as it does unconfined -- only its ability
to *write outside the worktree* is removed. `HOME_STATE` and `PROC_TMP` are the
two narrow, named locations a real headless turn must persist to (its session
state and scratch temp); without them the wrapped CLI could not run.

### Canonical paths are mandatory

The kernel evaluates the **symlink-resolved** path. On macOS `/var` resolves to
`/private/var` and `/tmp` to `/private/tmp`, so a rule written against an
un-resolved param never matches the real target and *every* write -- even inside
the worktree -- is denied. Both the adapter (`_canonical`) and the tests
`realpath` every path before passing it. This is the single most common way to
get a silently-broken profile, so it is called out here and enforced in code.

## How it is verified

`reference/cec/phase3/tests/test_phase3_sandbox.py` runs the **shipped** profile
and the argv the adapter actually composes, proving both directions:

- `test_worktree_rooted_write_succeeds` -- a nested `mkdir`+write inside the
  worktree returns 0 and the file lands.
- `test_out_of_worktree_write_fails` -- a write to a sibling of the worktree is
  denied (`Operation not permitted`), non-zero exit, file absent.
- `test_argv_wraps_claude_in_the_worktree_sandbox` -- `_argv` is the
  `sandbox-exec` invocation with the realpath'd `WORKTREE_ROOT`, so the
  confinement is what launches, not a profile that exists but is never applied.

The tests skip off macOS.

## Scope and limits (kept honest, not hidden)

- **Write boundary only.** This is not a general sandbox. Reads, process exec,
  and network are unconstrained by design, so it does **not** stop exfiltration,
  reading secrets the process can already see, or spawning arbitrary binaries. It
  stops out-of-worktree *writes*, which is the E4 scope and nothing more.
- **`HOME_STATE` / `PROC_TMP` are writable.** Writes to the CLI's own state dir
  (`~/.claude` or `$CLAUDE_CONFIG_DIR`) and to `$TMPDIR` are permitted so a real
  turn can function. A worker that wanted to escape via those paths is not
  stopped; they are trusted, narrow locations, not an arbitrary-FS grant.
- **The worktree gitdir is granted (`GITDIR_ROOT`), but NOT the object store (G3).**
  A linked worktree's real git directory lives at `<main-repo>/.git/worktrees/<name>/`,
  *outside* the worktree subtree. Finding G3: the first sandboxed worker went
  silent because `git` could not write `index.lock` there. `GITDIR_ROOT` (the
  4th `-D` param, the worktree's own gitdir) is now granted, so index writes —
  `git add -N` intent-to-add entries and the index-stat refresh a `git diff`
  performs — succeed. The shared object store (`<main-repo>/.git/objects/`) is
  **still denied**, so the worker can produce a complete diff via intent-to-add
  but cannot write a blob or actually commit content. The controller, not the
  worker, commits — that custody property is preserved by the object-store denial,
  not by denying the gitdir wholesale.
- **macOS only.** `sandbox-exec` is Apple's; off macOS `sandbox_wrap` is a
  no-op and the tests skip. The reference bridge runs on the Mac, where the
  confinement is enforced. `sandbox-exec` is formally deprecated by Apple but
  remains present and functional; if a future macOS removes it, the wrapper must
  move to the Endpoint Security / App Sandbox route, but the `_argv` seam and the
  profile's boundary stay the same.
- **Not a substitute for the controller's post-hoc diff check.** `allowed_paths`
  / `forbidden_paths` are still enforced by the controller comparing the diff
  after the turn. The sandbox is defense in depth for *where writes may land*,
  not a replacement for *which files the change may touch*.
