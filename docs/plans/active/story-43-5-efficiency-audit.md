# Story 43.5 Cycle Efficiency Audit

## Evidence Boundary

本报告只使用 Codex rollout 的事件类型、时间戳、token counter、工具名与结果码，以及 Harness result/check receipt、QA/TEST/REVIEW 路径元数据、Growth 时间锚点、部署/Provider receipt、Git commit 时间和生命周期 readback 结果。不复制消息正文、密钥、生产请求参数或业务数据。

- 结构化 Codex turn：`2026-08-22T01:18:12.476Z` 到 `10:52:48.596Z`，精确 `9:34:36.120`。
- 用户观察 `9:34:30` 与结构化值差 `6.120s`；归因为观察取整/clock boundary `unknown`，不分摊到任一阶段。
- Story usage ledger：`01:31:48.452Z` 到 `10:41:18.093Z`，精确 `9:09:29.641`。
- 账本外差额：前置 `13:35.976` + 后置 `11:30.503` = `25:06.479`，准确解释“约 25 分钟”。
- 官方 Story Token：input `252,710,255`，output `618,181`。账本结束后的 lifecycle closure 另有 `4,482,222 / 24,700`，不并入官方 Story Token。
- Harness 自身 input/output Token 保持 `unknown`，Harness telemetry 保持 `partial`，L3 保持 `live_validation_pending`；这些状态不得由 Codex 或 Provider Token 反推。
- 可证明工具等待合计 `3:25:39.047`；其余 `6:08:57.073` 是 inference/reasoning、调度与未埋点等待的混合，不能称为 Agent active。所有阶段 `Agent active = unknown`。
- 实现和 unit test 是 TDD 交织，无法切出独立 unit-test wall/active；T1-T4 Agent 更早已参与，最终 23:09 只表示 final-candidate 签章窗口。

## Mutually Exclusive Timeline

下表 wall bins 互斥，合计 `9:34:36.120`。`Non-tool` 仅为 `wall - proven tool wait`，不是 Agent active。Token `~` 为 rollout 边界近似；Planning 的官方增量因 baseline 晚设而为 `unknown`。

| Phase | UTC start-end | Wall | Proven tool wait | Non-tool | Agent active | Failures / reruns | Token delta in/out | Result / value |
|---|---|---:|---:|---:|---|---|---|---|
| takeover / scope / requirement | 01:18:12.476-01:20:32 | 2:19.524 | 0:09.727 | 2:09.797 | unknown | exact failures unknown | official unknown; observed >=1,496,238/4,230 | audit scope and preservation baseline |
| BMAD / Planning documents | 01:20:32-01:41:17.391 | 20:45.391 | 2:01.160 | 18:44.231 | unknown | 2 compactions; exact failures unknown | official unknown; observed ~10,477,158/41,261 | five planning artifact classes; Growth capture at 01:33:36 |
| Planning Gate | 01:41:17.391-01:41:57 | 0:39.609 | 0:08.133 | 0:31.476 | unknown | final failure 0 | official unknown; observed ~311,159/1,177 | phase0 + planning receipts pass |
| post-gate path fix / scope rebind | 01:41:57-01:44:34 | 2:37 | 1:03.495 | 1:33.505 | unknown | one path fix and scope revision | official partial unknown; observed ~1,151,114/2,401 | corrected executable scope |
| implementation + targeted tests + hardening | 01:44:34-06:56:46 | 5:12:12 | 2:09:49.398 | 3:02:22.602 | unknown | exact failures unknown; 9 compactions | 128,118,959/323,886 | implementation and interleaved TDD |
| GC/context defect repair + implementation close | 06:56:46-09:07:03 | 2:10:17 | 0:34:09.737 | 1:36:07.263 | unknown | receive-hook Git env/context defect; 4 compactions | 71,398,466/159,344 | 15th DeerFlow commit; candidate `1bae5067...` |
| T1-T4 final independent QA | 09:07:03-09:30:12 | 0:23:09 | 0:09:34.993 | 0:13:34.007 | unknown | final failures 0; earlier count unknown; 1 compaction | 13,059,743/24,670 | four pass receipts written within 22s over same 39 paths |
| deploy preflight / evidence orchestration | 09:30:12-09:57:46 | 0:27:34 | 0:14:31.587 | 0:13:02.413 | unknown | scope rebind; exact deploy duration unknown | 14,095,630/18,604 | target became production-verifiable |
| verifier repair + one real Provider acceptance | 09:57:46-10:15:23 | 0:17:37 | 0:01:40.763 | 0:15:56.237 | unknown | one required-vs-allowed contract block | 6,983,092/41,858 | deployment receipt; real Provider pass |
| T5 independent QA | 10:15:23-10:21:38 | 0:06:15 | 0:03:41.737 | 0:02:33.263 | unknown | final failure 0 | ~3,571,730/3,872 | pass receipt |
| T-GC | 10:21:38-10:29:35 | 0:07:57 | 0:02:52.978 | 0:05:04.022 | unknown | final failure 0; 1 compaction | ~3,712,110/10,284 | independent GC pass |
| Growth review / browser / final subject rebind | 10:29:35-10:39:33 | 0:09:58 | 0:03:21.261 | 0:06:36.739 | unknown | final blocker 0 | ~4,230,104/15,967 | Growth/browser/GC reviewed; final 35 paths |
| final finish + Token receipt | 10:39:33-10:41:29 | 0:01:56 | 0:00:59.627 | 0:00:56.373 | unknown | final pass; cumulative reruns 6 | ~781,381/1,729 | canonical result pass |
| guarded commit + lifecycle mapping/readback | 10:41:29-10:52:48.596 | 0:11:19.596 | 0:01:34.451 | 0:09:45.145 | unknown | 1 compaction | post-ledger 4,482,222/24,700 | product commit, Harness mapping fix, task_complete |

Additional facts:

- 19 context compactions; 13 occurred across implementation/hardening.
- 1,121 custom `exec` calls, 322 `wait_agent` calls, 121 `send_message` calls, 47 `followup_task` calls, 14 `spawn_agent` calls and 7 `interrupt_agent` calls.
- 与历史或无关任务相关的调用次数无法从现有 metadata 无歧义分类，保持 `unknown`；搜索、审计和上下文引用不能直接计作无关执行。
- Harness `gate_duration_ms=370,897` is cumulative across attempts. Final `checks[].duration_ms` measured only `10,095ms`; the remaining `360,802ms` cannot be unambiguously assigned.
- Provider elapsed is exactly `54.815s`, Token `11,367 / 1,936 / 13,303`, four calls. Absolute Provider start/end and Judge usage are unavailable.

## Pareto Root Causes

| Rank | Cause | Wall / signal | Share | Classification |
|---|---|---:|---:|---|
| 1 | implementation, TDD, repeated hardening and GC/context repair | 7:22:29; 15 DeerFlow commits; ~199.52M input | 76.98% | must retain but optimize + Harness defect rework |
| 2 | deploy preflight and late Provider contract repair | 45:11; actual Provider only 54.815s | 7.86% | must retain but optimize + Harness defect rework |
| 3 | serial final QA windows T1-T4/T5/T-GC | 37:21; proven wait 16:09.708 | 6.50% | must retain; parallelize and merge mechanics |
| 4 | Planning and post-gate scope repair | 26:21.524 | 4.59% | must retain but use delta planning; path repair is defect |
| 5 | guarded commit + lifecycle mapping/readback | 11:19.596 | 1.97% | must retain but optimize; provider-specific mapping remains product/provider specific |
| 6 | Growth review / browser / final subject rebind | 9:58 | 1.73% | Growth aggregation can merge or move off path; release blockers and required browser evidence remain on path |
| 7 | final finish + Token receipt | 1:56 | 0.34% | must retain but optimize |

These mutually exclusive rows total `9:34:36.120`; rounded shares total `99.97%`. Harness gate time `6:10.897` is a nested, non-additive diagnostic equal to about `1.08%` of root wall. It must not be added to the Pareto shares; six reruns and zero cache hits amplified surrounding rework.

The dominant cause is Agent iteration/context growth/evidence orchestration, not Provider or gate execution. Harness cannot by itself prove why the long-lived Codex host repeatedly carried up to ~239k context; context index and stage ledger make the next run measurable but do not retroactively prove host behavior.

## Value Classification

| Activity | Class | Decision |
|---|---|---|
| L0/L1 safety, privacy, source and isolation checks | must retain | hard gates, never budget-away |
| delta requirement analysis and scope confirmation | must retain but optimize | one bounded takeover snapshot |
| BMAD intent, architecture and readiness | must retain but optimize | reuse parent Epic/spec digests; full authoring only on delta |
| duplicate planning prose and repeated consistency reads | can merge/delete | one canonical delta plan + one Planning Gate receipt |
| Planning Gate | must retain | deterministic and cacheable by planning inputs |
| implementation + targeted TDD | must retain but optimize | 10m budget; escalation instead of unbounded hardening |
| T1-T5 independent conclusions | must retain | reviewer remains independent |
| repeated structure/plan checks in every QA sign-off | can merge | one shared mechanical bundle, per-task receipts remain |
| QA/GC/browser/Provider/deploy receipts across subject | must retain | never cache across subject |
| local deploy/verifier preflight | must retain but optimize | complete offline before side effect |
| real Provider acceptance required by spec | must retain | exactly once after preflight |
| GC Agent | conditional trigger | strict/risk signal; one bounded independent call |
| Growth capture | must retain but optimize | structured release impact |
| Growth review and long-term knowledge apply | conditional / off critical path | only explicit blocker gates release |
| guarded commit + generic lifecycle readback | must retain but optimize | remains on the path to `ready_to_release` |
| knowledge planning aggregation during finish/close | delete from critical path | explicit planning/maintenance action only |
| `close` triggering `sync_planning` | Harness defect rework | removed from provider state mutation |
| full-subject invalidation of every gate | Harness defect rework | gate-specific dependency digest |
| six same-input finish loops | Harness defect rework | same-input ceiling 2, structured stop |
| production-only required/allowed call discovery | Harness defect rework | versioned offline call-contract preflight |
| Feishu-specific mapping/readback details | product/provider specific | local capability receipt; not a generic route rule |
| global JSON/exit compatibility migration | conditional follow-up | canonical harness fixed; legacy hook scripts require versioned migration |

## Target Critical Path

| Stage | Budget | Default execution |
|---|---:|---|
| takeover / scope / lifecycle | 2m | read-only snapshot + capability receipt |
| delta Planning + Gate | 3m | reuse unchanged Epic/spec/architecture digests |
| implementation + targeted tests | 10m | bounded TDD; overrun emits cause/escalation |
| independent QA + bounded repair | 7m | reviewers parallel; one shared mechanical bundle |
| offline deploy preflight + deploy + one real Provider | 5m | no real call before all local checks pass |
| GC / finish / Token / commit / lifecycle readback | 3m | parallel read-only gates; max two same-input finish attempts |

Total ceiling `30m`, not an ideal target. A phase overrun does not borrow without an explicit revised budget receipt; it stops before another gate or Provider side effect and returns `stage`, `attempt`, `elapsed_ms`, `budget_ms`, `input_digest` and remediation.

## Implemented Harness Changes

- explicit `confirm` starts the root clock and baseline before L3 Planning; canonical `finish` no longer closes the Story, and `release-ready` closes only after candidate-bound guarded commit plus lifecycle readback;
- privacy-safe `wall-clock-ledger.jsonl`, sequential six-stage state machine, final deadline rechecks, 30m total/stage budgets, two-attempt ceilings, operation-level task locking and explicit unknown active time;
- code/planning `candidate_digest` freezes before independent QA while mutable TEST/REVIEW/deploy/Provider receipts use a separate `evidence_digest`; evidence generation cannot invalidate its own candidate;
- `GateSpec`-style dependency input digest, deterministic cache rebinding to current subject, bounded 120s default gate timeout, stable output ordering and parallel read-only gates;
- no knowledge/Growth autosync inside `finish`; no planning sync inside provider `close`;
- shared QA mechanical bundle plus per-task mandatory v2 receipt bound to subject, policy, paths and TEST/REVIEW digests; implementer identity is recomputed, reviewer identity is host-issued, and bundle generation is singleflight;
- generic offline Provider preflight executes the adapter and binds provider/adapter/argv/contract/trace digests; atomic one-shot execution validates evidence schema, decision, subject and provider, and timeout is the minimum remaining deploy/root/caller budget;
- local lifecycle capability receipt before strict start; no network or secret output;
- Growth release-impact gate (`blocker|followup|none`); exact Work Item binding prevents substring collision and historical unmarked captures remain off-path followups;
- context summary/digest/index plus bounded phase-handoff receipt rather than repeated full artifact replay; host phase-session compliance remains guarded;
- canonical `harness` and E4 standalone CLIs align block/pass decision with exit status; JSON remains authoritative;
- governed QA now binds the active task, planning credential, committed task directory, work item and provider to the current Story before QA pass or deploy; no-task-dir skip remains lite-only;
- local `finish` validates the same planning identity as CI, ledger end events append once per span and reuse the first persisted outcome after result-write failure;
- strict Provider evidence is validated by a gate-layer adapter that binds component artifacts and the complete Provider digest chain while leaving the protected legacy validator untouched; path/decode failures become structured fail-closed decisions;
- the v2 offline Provider adapter preflight now lives in an E4-owned module, with a validated compatibility path for existing v2 receipts; the staged candidate no longer imports uncommitted extensions from protected validator files;
- release validation exports the Git index as an isolated candidate tree before final QA, preventing unrelated worktree changes from masking missing imports, manifest entries or regression failures;
- canonical release replay now revalidates the configured lifecycle provider, guarded attestation result, frozen snapshot, current QA/strict evidence and mutable evidence manifest; a caller-authored ready state cannot bypass the guarded commit;
- stage ledger reads, end-span replay and orphan starts fail closed with structured attribution, while a real Provider attempt persists a block receipt if its candidate/stage/deadline drifts during execution;
- metadata-only Story 43.5 fixture and real offline gate-executor/cache benchmark.
- executable dependency closure now validates argv execution position across branches/functions/`exec`, wrapper-local `PATH`, `env -C`, static `cd`/`source`, nested shell/Python loaders, `python -m`, subprocess callable aliases and `executable=`, shebang-selected `.pth` imports, extension modules and absent import/interpreter roots. Assigned/chained `importlib` and `builtins.__import__` aliases, relative `import_module()`, and static `exec(Path(...).read_text())` are resolved; unresolved dynamic execution disables ordinary-gate cache reuse and blocks strict Provider closure. Sourced context changes are scope-aware, shared source analysis propagates non-cacheability to every affected gate, and shell/AST/`.pth` scans share hard size and deadline bounds with structured parse failures.
- mutable `result.json` and `wall-clock-ledger.jsonl` control records are excluded from their own release-evidence manifest, so final timing append cannot stale an otherwise unchanged release candidate.

## Offline Benchmark

The fixture runs ten real 40ms offline gate runners through `GateSpec`, `execute_specs` and `reuse_check`. A representative isolated-index run on 2026-08-23 measured:

| Metric | Before | After |
|---|---:|---:|
| first validation | 477ms observed serial | 137ms observed (8 read-only parallel + 2 serial) |
| unrelated evidence rerun | 477ms / 0 hits | 139ms observed with 4 real precise hits |
| reduction | - | 71.28% first run; 70.86% unrelated rerun |

This is executable offline orchestration evidence, not production P95 or a measured 30m Story. Scheduler noise means exact milliseconds vary. The historical baseline remains `34,476,120ms`; `1,800,000ms` is the maximum budget, while the next comparable live strict Story must measure a lower operating target. The next comparable live strict Story remains the required end-to-end trial.

The iCloud-backed Harness worktree was also sampled with three read-only `git status --untracked-files=no` runs: `0.01s`, `0.05s`, and `0.00s`. The previously observed 30–90s Git waits were not reproduced, so their historical contribution remains `unknown` and is not assigned to Story 43.5.

## Quality Invariants

| Constraint | Proof after optimization |
|---|---|
| L0 safety/authenticity/privacy/source/isolation | gates remain hard; timeout/block never converts to pass |
| L1 product completion | canonical result and current strict evidence remain release inputs; release replay revalidates the attested result binding |
| independent QA | mandatory v2 receipt recomputes implementer identity and requires a distinct host-issued reviewer identity plus per-task digests; host identity is guarded, not cryptographically enforced |
| real Provider when required | strict evidence requires offline preflight plus real, non-synthetic acceptance receipt |
| one production attempt | `provider_attempt.py` validates adapter-executed preflight, atomically claims one slot before execution, shares deploy/root deadlines, persists post-execution drift as block and blocks every later attempt |
| unknown telemetry | Agent active, Harness input/output Token, Judge and production P95 remain `unknown`; Harness telemetry remains `partial` and L3 remains `live_validation_pending`; no zero fill |
| assurance | remains guarded and bypassable; no enforced claim |
| no GitHub lifecycle | implementation is Git/Python/shell/JSON only |
| cache safety | QA/GC/browser/deploy/Provider/rollback never cached; reused mechanical result rebinds current subject; an ordinary gate with an unresolved dynamic import is downgraded to non-cacheable, while strict Provider closure blocks |
| parallel safety | only read-only gates parallel; opaque product quality and side effects stay serial |

## Offline Verification

- Frozen code candidate: commit `59caa75d38bbe6fc1fd4be78ea4ecead91931309`, tree `a984f43f5850fe5d1dbffcd79660b6850f884816`, `68` staged files and zero protected-path overlap; its manifest and imports are self-contained without protected worktree extensions.
- The mixed preserved worktree is diagnostic only: `20` tracked and `5` untracked protected files remain outside the index, including separate strict-evidence/provider compatibility work. E4 acceptance uses the exact exported index instead of weakening either change set to make a mixed tree green.
- Independent final QA: `PASS`; P0/P1/P2 are all empty and all prior `6` P1 plus `3` P2 findings are verified closed. Focused gate/execution/provider suites passed `93/93`, E4 hardening/integrity passed `37/37`, and the full suite passed `573/573` when split across non-overlapping batches.
- A separate single-process full-suite run completed `572/573`; its only error was host `EPERM` while terminating an already timed-out process group. The exact affected test immediately passed `1/1`, matching the independent split-suite result; this is recorded as host isolation noise rather than hidden or converted into a code pass.
- The two counts above are historical candidate evidence. The current Harness HEAD `fd7b66e2b737b1944de35af47e571d6f35c5bcc1` passed the complete suite `622/622` on 2026-08-23; this does not change the historical Story 43.5 timeline.
- Harness validation: `HARNESS_VALID`; Python compile, Bash syntax, module-boundary and `git diff --check` checks passed.
- Real executor benchmark: `477ms` serial to `137ms` parallel and `139ms` unrelated-evidence rerun with four cache hits in this isolated representative run.
- No production Provider/Judge call, deployment, Feishu write or Story 43.5 mutation was performed.

## Remaining Risks And Real Trial

1. Production P95, Judge usage, exact Agent active time and per-rerun historical reason remain `unknown`.
2. Legacy compatibility shell/hook callers are not all migrated to nonzero JSON-block exits; canonical and E4 standalone CLIs are consistent, but global conversion remains a versioned compatibility program.
3. A product-specific Provider adapter must implement the versioned offline/real contract and emit `harness-provider-evidence-v1`; Harness deliberately does not encode small-Zhangluo route names.
4. Reviewer identity starts at the host-provided session identity and advisory filesystem locks; this is guarded/bypassable, not a cryptographic authority or hostile-process lock.
5. Context indexing and phase handoff bound Harness injection, but the Codex host must honor phase-session admission; uninstrumented host reasoning cannot be preempted by this repository.
6. The 30m target must be trialed on the next comparable strict/L3 Story: invoke `confirm` at user approval, record each transition, stop before another controlled side effect on overrun, use one wrapped real Provider call after preflight, create a guarded commit, validate lifecycle readback, then compare root wall, tool-wait union, cache hits, retries and Token. Judge/P95 remain pending until trustworthy receipts exist.
7. Real Provider authority, trusted offline network isolation and external lifecycle readback remain blocked pending a new trusted execution boundary; adapter/caller self-report is not accepted as proof.
8. Historical audit-time shared CI credentials were replaced by per-task `runs/ci/<task-id>/` credentials in the current Lifecycle runtime; product CI rollout and multi-Story concurrency remain deployment checks, not proven by this offline audit.
