/**
 * Swarm Out service: tasks dispatched to Hives and the questions they wait on.
 *
 * Backend: /api/swarm-out (backend/routers/swarm_out.py). These routes never return
 * a Hive's address or password; every call goes through the local daemon.
 */
import api from './api';

export interface SwarmOutRun {
  dispatch_id: string;
  hive_id: string;
  hive: string;
  /** null when the Hive never named the session (the dispatch timed out) */
  session_id: string | null;
  title: string;
  origin: string;
  sent_at: string;
  /** the Hive never named the session (the dispatch timed out): it may be running */
  session_unknown?: boolean;
  /** From the Hive's own report of the session (backend swarm_out.run_state), the one
   *  field every reader of "is it running / waiting" reads. "stopped" = not working
   *  now: the Host cannot tell finished from failed or stopped. "unknown": the Hive could
   *  not be asked, or the session was never named. */
  state: 'waiting' | 'running' | 'stopped' | 'unknown';
  /** why: e.g. waiting_input, question, preparing, idle, not_held, hive_stopped,
   *  hive_silent, hive_updating (the Hive is updating), no_session_id */
  state_reason: string;
  /** the repositories the task declared, with the directory each goes to on the Hive */
  repos?: { url: string; ref: string | null; dir: string }[];
  /** The Project Agent the task went under (this machine's own dispatch record; absent
   *  for a dispatch from before Project Agents). brain "General" = the Runner Agent. */
  project_agent?: SwarmOutAgent;
  /** How the run ended and what it did, as last read from its Hive (backend
   *  core/swarm_out_results.public): no reply text, no error message. */
  result?: SwarmOutResult | null;
  result_status?: 'fetched' | 'pending' | 'failed' | 'not_applicable';
  /** why the last check failed: session_gone, hive_silent, http_502, ... */
  result_error?: string | null;
  result_checked_at?: string | null;
  /** Where the run stands for the person, and the commands they can give in it, in
   *  their order (backend core/swarm_out_settle.derive; design
   *  docs/design/2026-10-31-swarm-out-run-review.md §1.2). The panel renders these and
   *  decides nothing itself. Absent for a dispatch from before Project Agents. */
  stage?: SwarmOutStage;
  actions?: SwarmOutAction[];
  /** why a run is stopped (no_session, no_result, hive_gone, or how its result ended),
   *  or why it is closed (discarded, reverted, expired, hive_gone) */
  why?: string | null;
  settle?: SwarmOutSettle;
  /** not settled, and this Host removes its records within 3 days (bundles can no
   *  longer land after that) */
  cleanup_soon?: boolean;
  /** The run's decision funnel (backend core/swarm_out_record.funnel; design
   *  docs/design/2026-11-02-hive-run-decision-record.md): the gates passed and the one
   *  it is at. Absent from an older backend. */
  funnel?: SwarmOutFunnel;
  /** How many decisions the person made on the run (their commands, answers, what they
   *  said in its Hive tab); null for a record kept before the funnel. */
  person_decisions?: number | null;
  /** The person has something to do on it now (an open ask, a wait that is theirs);
   *  null: no live session answers for the run, the older rules apply. */
  needs_person?: boolean | null;
}

export type SwarmOutGateKey = 'review' | 'land' | 'judge';

/** One gate of the funnel: done, the one it is at (now), or waiting on the person. */
export interface SwarmOutGate {
  key: SwarmOutGateKey;
  state: 'done' | 'now' | 'person';
  by: 'agent' | 'person' | 'product' | null;
  at: string | null;
  /** land: the commit or the first file placed */
  ref: string | null;
}

/** A landed result the dispatching agent handed to the person (backend
 *  swarm_out_record.ask_person). The text is the agent's own, never the Hive's. */
export interface SwarmOutAskPerson {
  state: 'open' | 'keep' | 'revert' | 'reverted';
  decide: string;
  judgment: string;
  why: string;
  landed: { commits: string[]; files: string[] };
  at?: string;
  answered_at?: string;
  note?: string;
}

export interface SwarmOutFunnel {
  gates: SwarmOutGate[];
  current: SwarmOutGateKey | null;
  ask: SwarmOutAskPerson | null;
  reruns: number;
}

/** One decision on a run (backend core/swarm_out_record): who, when, which decision,
 *  their reason, and the facts this host saw then. */
export interface SwarmOutDecision {
  at: string;
  by: 'agent' | 'person' | 'product';
  session?: string | null;
  decision: string;
  why?: string;
  observed?: Record<string, unknown>;
  command?: string;
  request_id?: string;
  class?: string | null;
  pick?: string;
  asked?: string;
  commits?: string[];
  failed?: boolean;
}

/** ``closed``: discarded, reverted, expired or its Hive gone; which one is the row's
 *  ``why`` (design 2026-11-02-swarm-out-hive-deaths-diagnostics-states §3 S1). */
export type SwarmOutStage = 'running' | 'needs_answer' | 'awaiting' | 'stopped' | 'accepted' | 'closed';

/** Why a ``closed`` run is closed (its ``why``). */
export type SwarmOutClosedWhy = 'discarded' | 'reverted' | 'expired' | 'hive_gone';

export type SwarmOutCommand = 'accept' | 'rerun' | 'discard' | 'stop' | 'open_session' | 'answer'
  | 'keep' | 'revert';

/** One command on a run. ``variant``: accept ``land`` (land the commits that came
 *  back), ``manual`` (they cannot be taken as they are: "I landed it by hand"),
 *  ``verdict`` (pass on the answer or document); open_session ``read_only`` (accepted). */
export interface SwarmOutAction {
  key: SwarmOutCommand;
  primary: boolean;
  /** hint: not a command, a pointer to where the person acts (the wait card) */
  variant?: 'land' | 'manual' | 'verdict' | 'read_only' | 'hint';
}

export interface SwarmOutSettle {
  /** unsealed: commits the Hive could not seal into a bundle (they exist only in its
   *  workspace: land them by hand, or discarding removes them); rejected: an older
   *  Host's record */
  code: 'none' | 'landed' | 'rejected' | 'waiting' | 'unsealed';
  /** what still holds it back from settling (files: a returned file not yet placed here) */
  why_not: ('verdict' | 'code' | 'files' | 'lessons')[];
  /** settled: the Hive was told to remove its workspace (released false: not yet, told
   *  again after a backoff; null: no Hive to tell) */
  settled: { at: string; outcome: 'passed' | 'abandoned' | 'gone' | 'expired' | 'reverted'; released: boolean | null } | null;
}

export type SwarmOutEnded = 'completed' | 'turn_limit' | 'error' | 'crashed' | 'paused' | 'no_terminal'
  | 'stopped' | 'restarted' | 'superseded' | 'gave_up' | 'no_record';

/** One declared repository: what the task changed against the version it pulled.
 *  In /runs the file and commit lists are empty (counts only) and url/ref/dir are
 *  null unless still URL-shaped; /result carries them. */
export interface SwarmOutRepoFact {
  url: string | null; ref: string | null; dir: string | null;
  present: boolean | null; origin_matches: boolean | null;
  base: string | null; head: string | null;
  commits: string[]; commits_total: number;
  files: { path: string; added: number | null; deleted: number | null }[];
  files_total: number; added: number; deleted: number; untracked: number;
  pushed: 'yes' | 'no' | 'nothing_to_push' | 'unknown';
  error: string | null;
  /** The Hive's commits as a bundle the host can take back: /result {key, sha256,
   *  size} or {key, error}; /runs {available} or {available: false, error}. */
  bundle?: { key?: string; sha256?: string; size?: number; error?: string; available?: boolean } | null;
}

/** detail: /result only (null in /runs) */
export interface SwarmOutAuditRow { dim: string; verdict: string | null; detail: string | null }

export interface SwarmOutAgent {
  brain: string;
  mode: string;
  revision: string;
  profile: string;
  /** only on a run dispatched before 2026-11-01 (a bundle is the one delivery there is) */
  delivery?: string;
  acceptance: string;
  /** what comes back: an answer, a document, or commits (absent before run-spec r4) */
  returns?: 'answer' | 'document' | 'commits';
  gates?: string[];
  validations?: { id: string; command: string }[];
}

/** ran: the run's test record or a Bash call ran the check's command and did not error;
 *  errored: its last such run errored; unknown: that run has no recorded result;
 *  not_seen: the Host found no record that ran it (a check run in a loop or a script is
 *  not seen: it is never "not run"). Never "passed". */
export type SwarmOutCheckStatus = 'ran' | 'errored' | 'unknown' | 'not_seen';

export type SwarmOutExecutionDiff = 'made_a_run' | 'no_run' | 'other_profile' | 'other_run';

export interface SwarmOutResult {
  ended: SwarmOutEnded;
  /** may still change (no ending recorded after the last message, or paused) */
  provisional: boolean;
  /** the Hive wrote how the turn ended (false: a Hive version without that record) */
  outcome_recorded: boolean;
  /** some turn of the session has an outcome: this Hive records them */
  hive_records_outcomes: boolean;
  /** /runs: {code} ("other" unless a plain code); /result: {code, message} */
  error: { code: string | null; message?: string | null } | null;
  ended_at: string | null;
  turns: number;
  older_turns_not_read: boolean;
  active_s: number | null;
  /** every kind of token the run used: input not read from the cache, output, and the
   *  prompt cache's reads and writes. The cache fields are null on a record kept before
   *  they were counted: its total is not known. */
  tokens: { input: number; output: number; cache_read: number | null; cache_write: number | null } | null;
  /** the tokens are a lower bound: the usage ledger's rows for the run have not arrived
   *  (or caught up), and the record's own counts leave out sub-agents */
  tokens_partial?: boolean;
  /** the models the run's spend was on (registry short names, "other" for an unknown
   *  one), from the usage ledger; null before its rows arrive */
  models?: string[] | null;
  cost_usd: number | null;
  /** the cost may be short (a turn without a record, or older turns not read) */
  cost_partial: boolean;
  /** /result only: the last reply, whole */
  report?: string;
  report_chars: number;
  kinds: ('pipeline' | 'code' | 'files' | 'answer')[];
  work: {
    repos: SwarmOutRepoFact[] | null;
    /** /runs: root null, repo null unless a plain name */
    changes: { root: string | null; repo: string | null; files: number; added: number; deleted: number; landed: string[] }[];
    /** /runs: kind only; /result: with the Hive path */
    /** ``local``: where the file is on this machine when it came back (core/hive_files) */
    files: { path?: string; local?: string; not_placed?: boolean; kind: 'content' | 'knowledge' | 'external-diff' | 'external-nodiff' | 'other' }[];
    files_omitted: number;
    steps: { total: number; failed: number; agents: number; by_tool: Record<string, number> };
    /** the person in the loop: commands approved / denied, asks left unanswered,
     *  questions answered, and ``said``: every message they wrote in the session after
     *  the task (a re-run with a correction, or anything typed in the Hive tab; absent
     *  on a record kept before it was counted) */
    human: { approved: number; denied: number; unanswered: number; answered_questions: number; said?: number };
    /** waits the dispatching agent answered itself: its own decisions, not the person stepping in */
    host_handled?: number;
  };
  pipeline: { run_id: string | null; profile: string | null; status: string | null; landing: string | null;
    landed_sha: string | null; audit: SwarmOutAuditRow[] } | null;
  dropped: number;
  /** from this machine's dispatch record (null for a dispatch without a Project Agent) */
  project_agent?: { brain: string | null; mode: string | null; profile: string | null; delivery: string | null } | null;
  checks?: { id: string; status: SwarmOutCheckStatus }[] | null;
  /** Brain gate refusals by gate ("error:<gate>": the gate could not run; "integrity":
   *  the gates were changed or unreadable); null: the Hive does not count them */
  gate_refusals?: Record<string, number> | null;
  /** older turns were not read: a check or refusal there is not seen */
  checks_partial?: boolean;
  /** the check statuses were inferred from the commands' text (the Hive keeps no
   *  record of its checks); absent from an older backend */
  checks_inferred?: boolean;
  /** The statuses are the Hive's own check record (scripts/task_checks), not proof. */
  checks_from_record?: boolean;
  /** did the run run the way it was dispatched to (backend execution_check; run-spec
   *  §4.2): ``not_yet`` while a pipeline task's run has not shown up; null for a row
   *  from before the choice was made per dispatch */
  execution_check?: { state: 'matches' | 'differs' | 'not_yet'; why: SwarmOutExecutionDiff | null } | null;
  /** the run's lessons (backend core/hive_lessons): how many the Hive holds, how many went
   *  to this machine's judge and what it decided; null: not a Project Agent run */
  lessons?: SwarmOutLessons | null;
}

export interface SwarmOutLessons {
  /** kept on this machine; not yet queued for this machine's CDF (it was unavailable:
   *  queued at the next check); accepted: some were queued (no one takes them, CDF
   *  design D13) */
  returned: number; pending: number; accepted: boolean;
  /** sent to this machine's judge, and what it decided */
  sent: number; written: number; rejected: number; judging: number;
  skipped: number; refused: number; truncated: boolean; error: string | null;
  /** the units not yet queued, as this machine's hashes (no Hive text) */
  digests?: string[];
}

/** A wait the agent that dispatched the run answered itself (backend
 *  swarm_out_actor.host_answers): what was asked (the Hive's words, plain text), the
 *  option it picked and why. */
export interface SwarmOutHostAnswer {
  at: string | null;
  request_id: string | null;
  class: string | null;
  asked: string | null;
  pick: string | null;
  why: string | null;
}

/** The whole kept record of one run (GET /swarm-out/runs/{id}/result). */
export interface SwarmOutResultRecord {
  dispatch_id: string;
  record: { result: SwarmOutResult | null; fetch_error: string | null; checked_at: string | null;
    fetched_at: string | null } | null;
  /** an older backend sends none */
  host_answers?: SwarmOutHostAnswer[];
  /** every decision on the run, oldest first (an older backend sends none) */
  decisions?: SwarmOutDecision[];
  ask_person?: SwarmOutAskPerson | null;
}

export interface SwarmOutQuestion {
  question: string;
  header: string;
  multiSelect: boolean;
  options: { label: string; description: string }[];
}

/** One option of a pipeline run's decision (the run's DC-1 options). */
export interface SwarmOutDecisionOption {
  id: string;
  choice: string;
  consequence: string;
  reversible: boolean;
  risk: 'low' | 'high';
}

export type SwarmOutWaitPhase = 'waiting' | 'sent' | 'taken' | 'refused' | 'gone';
export type SwarmOutRefusalReason = 'no_option' | 'changed' | 'too_long' | 'run_refused' | 'not_recorded'
  | 'not_host_class' | 'other';

export interface SwarmOutAsk {
  /** decision: a pipeline run on the Hive waits on a person's pick (open while the run waits) */
  kind: 'question' | 'approval' | 'decision';
  hive: string;
  hive_id: string;
  session_id: string;
  request_id: string;
  asked_at: string;
  questions?: SwarmOutQuestion[];
  tool_name?: string;
  command?: string;
  reason?: string;
  /** the command (or a decision's text) was longer than the card shows: it cannot be approved or answered here */
  truncated?: boolean;
  /** what the card shows; an approval sends it back (the Hive delivers only on a match) */
  shown_sha256?: string;
  /** the dispatch this ask belongs to, or null for a session this Host did not send */
  dispatch_title?: string | null;
  /** a dispatch of this Hive whose session was never named, which may own this ask */
  possible_dispatch_title?: string | null;
  /** the Hive refused the answer given (it did not match what was waiting): not delivered */
  refused?: boolean;
  /** what the wait is about, as the Hive's gate said (design 2026-10-31-hive-approvals):
   *  the agent that dispatched the run answers question / decision / hive_local /
   *  run_authorization itself; null from a Hive too old to say (the person's) */
  approval_class?: string | null;
  /** answered: by the person, or by the agent that dispatched the run (its own decision) */
  answered_by?: 'person' | 'host';
  /** where the wait is (design 2026-10-31-swarm-out-single-loop-fixes §2.1): ``asks``
   *  holds waiting and refused, ``answered`` holds sent, taken and gone */
  phase?: SwarmOutWaitPhase;
  /** refused: why the Hive did not use the answer (a code; the Host words it) */
  refused_reason?: SwarmOutRefusalReason;
  /** what the person chose (from the Host's own answer file) */
  chosen?: string;
  /** answered: the question (a decision's or a question's) or an approval's command */
  question_text?: string;
  answered_at?: string;
  /** questions / options the Host did not list */
  omitted?: { questions: number; options: number };
  /** decision: the run, its question, why the evidence was not enough, its options */
  run_id?: string;
  question?: string;
  why?: string;
  options?: SwarmOutDecisionOption[];
}

export interface SwarmOutRuns {
  runs: SwarmOutRun[];
  asks: SwarmOutAsk[];
  /** the waits answered from here (sent, taken, gone), for the cards' one line; absent
   *  from an older backend */
  answered?: SwarmOutAsk[];
  errors: string[];
  /** tasks refused at intake, all time (dispatch-center §2.3); ``private``: of them,
   *  because they need a private network. null: unreadable; absent: older backend */
  refusals?: { total: number; private: number } | null;
}

export interface SwarmOutHive {
  name: string;
  status: string;
  region: string | null;
}

/** One Hive in the panel's fleet: names, status and the load a running one reports
 *  (null when it is not running, or did not answer). Never an address or credential. */
export interface SwarmOutFleetHive extends SwarmOutHive {
  version: string | null;
  instance_type: string | null;
  /** null for a Hive that is not running */
  reachable: boolean | null;
  /** every session streaming on the Hive (its own jobs and web sessions too) */
  streaming: number | null;
  /** a new task would queue: the Hive's memory is under critical pressure */
  saturated: boolean | null;
  /** the Hive's memory in use, 0-100; null when unknown (or an older Hive) */
  memory_percent: number | null;
}

export interface SwarmOutFleet {
  hives: SwarmOutFleetHive[];
  running: number;
}

export interface SwarmOutHives {
  hives: SwarmOutHive[];
  running: number;
}

/** One repository's commits taken back and checked on this machine (backend
 *  core/hive_return.prepare): what the person reviews before they land. Commit
 *  subjects are the Hive's text, shown as data. */
export interface SwarmOutReturn {
  key: string;
  tip: string;
  base: string;
  ref: string;
  branch: string;
  files: { path: string; added: number | null; deleted: number | null; protected: boolean }[];
  files_total: number;
  protected_files: string[];
  commits: { sha: string; subject: string }[];
  /** the checkout has uncommitted edits: landing may refuse if they touch these files */
  checkout_dirty?: boolean;
  status: 'prepared' | 'landed' | 'rejected';
}

/** One changed file of a prepared review, both versions (backend
 *  core/hive_return.file_versions): read from this machine's git objects. ``base`` /
 *  ``tip`` null: the file does not exist on that side (added / deleted), or it is
 *  binary or too large to show (no content then). The text is the Hive's: data only. */
export interface SwarmOutFileVersions {
  path: string;
  base_sha: string;
  tip_sha: string;
  base: string | null;
  tip: string | null;
  base_exists: boolean;
  tip_exists: boolean;
  binary: boolean;
  too_large: boolean;
}

/** Every command the App sends on a run is the person's: the backend limits a command
 *  that does not say so as an agent's (core/swarm_out_actor). */
const PERSON = 'person';

const runPath = (dispatchId: string, tail: string) => `/swarm-out/runs/${encodeURIComponent(dispatchId)}/${tail}`;

export const swarmOutService = {
  async returnPrepare(dispatchId: string, url: string): Promise<SwarmOutReturn> {
    const { data } = await api.post(`/swarm-out/runs/${encodeURIComponent(dispatchId)}/return/prepare`, { url, by: PERSON });
    return data as SwarmOutReturn;
  },
  /** Both versions of one file the prepared review lists (409: not taken back yet). */
  async returnFile(dispatchId: string, url: string, path: string): Promise<SwarmOutFileVersions> {
    const { data } = await api.get(runPath(dispatchId, 'return/file'), { params: { url, path } });
    return data as SwarmOutFileVersions;
  },
  async returnLand(dispatchId: string, key: string, tip: string):
  Promise<{ landed: boolean; head: string | null; into: string | null }> {
    const { data } = await api.post(`/swarm-out/runs/${encodeURIComponent(dispatchId)}/return/land`,
      { key, tip, by: PERSON });
    return data as { landed: boolean; head: string | null; into: string | null };
  },
  /** Accept: the person's pass on the result that ended at `endedAt` (the one shown). */
  async accept(dispatchId: string, endedAt: string): Promise<void> {
    await api.post(runPath(dispatchId, 'verdict'), { value: 'pass', note: '', ended_at: endedAt, by: PERSON });
  },
  /** Accept commits that cannot be taken as they are: the person landed them by hand. */
  async manualLanded(dispatchId: string): Promise<void> {
    await api.post(runPath(dispatchId, 'manual-landed'), { by: PERSON });
  },
  /** Re-run with a correction: the text goes into the run's own Hive session. */
  async rerun(dispatchId: string, text: string): Promise<void> {
    await api.post(runPath(dispatchId, 'rerun'), { text, by: PERSON });
  },
  /** The person's answer to a landed result the agent handed over: keep it, or have
   *  the agent revert it. */
  async answerAsk(dispatchId: string, pick: 'keep' | 'revert', note: string): Promise<void> {
    await api.post(runPath(dispatchId, 'ask-person/answer'), { pick, note });
  },
  /** Stop the run's current turn; its session and workspace stay. */
  async stop(dispatchId: string): Promise<void> {
    await api.post(runPath(dispatchId, 'stop'), { by: PERSON });
  },
  /** Discard the run: stopped, closed, its Hive workspace removed. Final. */
  async discard(dispatchId: string, note: string): Promise<{ discarded: boolean; stopped: boolean | null }> {
    const { data } = await api.post(runPath(dispatchId, 'discard'), { note, by: PERSON });
    return data as { discarded: boolean; stopped: boolean | null };
  },
  /** Every Hive with its load: what the panel's fleet and free slots read. No address
   *  or credential (the rows are built from an allow-list of keys). */
  async fleet(): Promise<SwarmOutFleet> {
    const { data } = await api.get('/swarm-out/fleet');
    return data as SwarmOutFleet;
  },
  /** Names and status only: the panel never sees a Hive's address or credentials. */
  async hives(): Promise<SwarmOutHives> {
    const { data } = await api.get('/swarm-out/hives');
    return data as SwarmOutHives;
  },

  async runs(): Promise<SwarmOutRuns> {
    const { data } = await api.get('/swarm-out/runs');
    return data as SwarmOutRuns;
  },

  /** One run's whole kept result, with the Hive's reply: the panel's card only. */
  async result(dispatchId: string): Promise<SwarmOutResultRecord> {
    const { data } = await api.get(`/swarm-out/runs/${encodeURIComponent(dispatchId)}/result`);
    return data as SwarmOutResultRecord;
  },

  /** Check one run's Hive now. */
  async refresh(dispatchId: string): Promise<{ result: SwarmOutResult | null; result_error: string | null }> {
    const { data } = await api.post(`/swarm-out/runs/${encodeURIComponent(dispatchId)}/refresh`);
    return data as { result: SwarmOutResult | null; result_error: string | null };
  },

  async answer(ask: SwarmOutAsk, body: { answers?: Record<string, string>; decision?: 'approve' | 'deny'; option_id?: string }): Promise<void> {
    const seg = [ask.hive_id, ask.session_id, ask.request_id].map(encodeURIComponent);
    // an approval and a decision name what the card showed (the Hive delivers only on a match)
    const payload = ask.kind === 'approval' || ask.kind === 'decision' ? { ...body, shown_sha256: ask.shown_sha256 } : body;
    await api.post(`/swarm-out/asks/${seg[0]}/${seg[1]}/${seg[2]}/answer`, payload);
  },
};
