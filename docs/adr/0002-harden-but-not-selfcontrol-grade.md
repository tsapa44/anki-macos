# Harden the Block with a root daemon, but stop short of SelfControl-grade

The Block is enforced by a privileged background service (root `launchd` daemon)
that re-applies the network block if tampered with, survives reboot and logout, and
refuses to lift until AnkiConnect confirms the Daily quota. We deliberately do NOT
pursue SelfControl-grade resistance: no clock-proofing, no recovery-mode-proofing,
no refusing-to-uninstall.

## Why

- A simple unprivileged toggle (editing `hosts`, killing a user process) is defeated
  by the very laziness the tool exists to counter - it would never survive a groggy
  morning.
- Full SelfControl-grade hardening is a security-engineering effort with real
  "I locked myself out of my own machine" risk and brittle coupling to OS internals,
  for marginal gain.

The deterrent only needs to beat a *lazy moment*, not a determined adversary -
because the sanctioned escape hatch is always "do your 20 Reviews," the thing the
user wanted to do anyway. Trapping yourself is therefore safe: the way out is the goal.

## Consequences

- Installing the daemon needs admin/sudo once.
- A determined user can still bypass (recovery mode, etc.). Accepted - that user has
  chosen to defeat themselves, which is out of scope.
