# P0 recovery and consistent backup

Recovery must preserve evidence and operator work. Never use broad process-name kills, delete WAL
files, reset the database or release an active lease to bypass admission.

## Worker/process failure

1. Keep the API available and inspect worker status, executions, resources and managed processes.
2. Pause admission. Cancellation and emergency stop are durable requests, not immediate proof of death.
3. Check terminal process evidence. Native recovery uses recorded PID plus creation time; unknown
   identity or access denied is unresolved, not permission to kill another process.
4. Restart the worker only with the same database and explicit ownership of the local instance.
   Leader expiry/takeover uses fencing; an expired worker cannot publish a late successful result.
5. Resume only after native cleanup/resource ownership is resolved. Preserve failed/truncated artifacts.

Windows crash tests exercise kill-on-close and descendant termination. The audit reports active
registered roots in the selected database; it is not a claim that every process on the host has been
enumerated or proven non-orphaned. POSIX native crash behavior remains a separate platform gate.

## Consistent database backup

Stop API and worker, let the leader lease expire, and verify no active/unreleased managed process
remains. `pause` alone does not stop the worker heartbeat and is insufficient for maintenance.

```powershell
uv run python -m local_control_center.quality.maintenance backup --source "C:\AIDO\platform.sqlite" --destination "C:\AIDO-backups\p0-before-upgrade"
uv run python -m local_control_center.quality.maintenance restore --source "C:\AIDO-backups\p0-before-upgrade" --destination "C:\AIDO-restored\p0-check"
```

Replace these example paths with your verified instance paths. Destination directories must be new;
there is no overwrite/in-place restore. Backup uses the [SQLite online backup API](https://www.sqlite.org/backup.html)
under writer locks, includes committed WAL state, checks integrity and hashes the resulting files.
A failure leaves an incomplete bundle for diagnosis and does not delete its source. Restore validates
manifest, permitted relative paths and hashes before copying to a new directory.

The bundle contains `platform.sqlite` and, if present, `.tmp/operation-inputs.sqlite` beside the source
database. It does **not** contain workspaces, Git worktrees, evidence artifact directories, provider
credentials, native keyring data or every external integration store. Preserve those separately with
their native supported backup procedure. DPAPI inputs remain tied to the Windows account; copying
the database to another account does not make those inputs decryptable.

Start the restored copy with explicit `--db-path` only after reviewing queued work and default safety
flags. Do not run two independently restored instances against the same external workspace. Rollback
means reopening the retained pre-upgrade copy with its matching code, not applying old code to a
newer schema or undoing migrations in place.

## Interrupted delivery landing

Delivery approval and its `landing.status=pending` intent commit atomically before any Git or network
operation. The queued worker performs the landing outside SQLite transactions and records its result
in the loop and feedback evidence. If that process crashes between approval and the final receipt,
`delivered` means the operator accepted the work, not that merge/push was proven successful. Inspect
the pending landing, managed-process evidence, worktree HEAD and remote state before an explicit
recovery. Do not automatically replay merge/push after an uncertain external effect.

## Disk and evidence retention

Host samples have bounded retention and output artifacts have per-stream limits. Automatic retention
for sealed operation inputs and complete evidence artifacts is not implemented in P0. Monitor disk
headroom; retain referenced artifacts for audit and backup before any operator-approved cleanup.
The governor's disk floor is a backpressure mechanism, not a backup/retention policy.

Verification reports live under `.tmp/operational-hardening-p0/`; unique run directories preserve
failed attempts. Root report files are latest-attempt indexes, not an append-only ledger. A missing
terminal report/JUnit means interrupted or unknown, never passed. Do not launch a duplicate gate
until checking whether the recorded owned process is still alive.
