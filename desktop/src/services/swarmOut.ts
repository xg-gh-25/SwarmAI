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
  waiting: boolean;
  /** Its session is streaming on the Hive now; null when that Hive could not be asked
   *  or the session was never named. */
  running: boolean | null;
  /** the Hive never named the session (the dispatch timed out): it may be running */
  session_unknown?: boolean;
  /** From the Hive's own report of the session (backend swarm_out.run_state). "stopped"
   *  = not working now: the Host cannot tell finished from failed or stopped. */
  state?: 'waiting' | 'running' | 'stopped' | 'unknown';
  /** why: e.g. waiting_input, question, preparing, idle, not_held, hive_stopped,
   *  hive_silent, no_session_id */
  state_reason?: string;
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
  delivery: string;
  acceptance: string;
  gates?: string[];
  validations?: { id: string; command: string }[];
}

/** ran: a Bash call ran the check's command (with any arguments) and did not error;
 *  errored: its last such call errored; unknown: that call has no recorded result;
 *  not_run: no call ran it. Never "passed". */
export type SwarmOutCheckStatus = 'ran' | 'errored' | 'unknown' | 'not_run';

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
  /** /result only: the last reply (up to 4000 chars) */
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
    human: { approved: number; denied: number; unanswered: number; answered_questions: number };
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
  /** did the run run the way it was dispatched to (backend execution_check; run-spec
   *  §4.2): ``not_yet`` while a pipeline task's run has not shown up; null for a row
   *  from before the choice was made per dispatch */
  execution_check?: { state: 'matches' | 'differs' | 'not_yet'; why: SwarmOutExecutionDiff | null } | null;
  /** the run's lessons (backend core/hive_lessons): how many the Hive holds, how many went
   *  to this machine's judge and what it decided; null: not a Project Agent run */
  lessons?: SwarmOutLessons | null;
}

export interface SwarmOutLessons {
  /** kept on this machine; waiting for the person to take them (nothing reaches a Brain
   *  before that); whether they took some */
  returned: number; pending: number; accepted: boolean;
  /** sent to this machine's judge, and what it decided */
  sent: number; written: number; rejected: number; judging: number;
  skipped: number; refused: number; truncated: boolean; error: string | null;
  /** the waiting units as this machine's hashes (no Hive text): what an accept takes */
  digests?: string[];
}

/** The whole kept record of one run (GET /swarm-out/runs/{id}/result). */
export interface SwarmOutResultRecord {
  dispatch_id: string;
  record: { result: SwarmOutResult | null; fetch_error: string | null; checked_at: string | null;
    fetched_at: string | null } | null;
}

export interface SwarmOutQuestion {
  question: string;
  header: string;
  multiSelect: boolean;
  options: { label: string; description: string }[];
}

export interface SwarmOutAsk {
  kind: 'question' | 'approval';
  hive: string;
  hive_id: string;
  session_id: string;
  request_id: string;
  asked_at: string;
  questions?: SwarmOutQuestion[];
  tool_name?: string;
  command?: string;
  reason?: string;
  /** the command was longer than the card shows: it cannot be approved here */
  truncated?: boolean;
  /** what the card shows; an approval sends it back (the Hive delivers only on a match) */
  shown_sha256?: string;
  /** the dispatch this ask belongs to, or null for a session this Host did not send */
  dispatch_title?: string | null;
  /** a dispatch of this Hive whose session was never named, which may own this ask */
  possible_dispatch_title?: string | null;
  /** the Hive refused the answer given (it did not match what was waiting): not delivered */
  refused?: boolean;
  /** questions / options the Host did not list */
  omitted?: { questions: number; options: number };
}

export interface SwarmOutRuns {
  runs: SwarmOutRun[];
  asks: SwarmOutAsk[];
  errors: string[];
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

export const swarmOutService = {
  async returnPrepare(dispatchId: string, url: string): Promise<SwarmOutReturn> {
    const { data } = await api.post(`/swarm-out/runs/${encodeURIComponent(dispatchId)}/return/prepare`, { url });
    return data as SwarmOutReturn;
  },
  async returnLand(dispatchId: string, key: string, tip: string, approveProtected: boolean):
  Promise<{ landed: boolean; head: string | null; into: string | null }> {
    const { data } = await api.post(`/swarm-out/runs/${encodeURIComponent(dispatchId)}/return/land`,
      { key, tip, approve_protected: approveProtected });
    return data as { landed: boolean; head: string | null; into: string | null };
  },
  /** Take a run's lessons: they go to this machine's judge (backend core/hive_lessons.accept). */
  /** Take the lessons the person was shown (``digests`` of the state rendered): one
   *  that arrived after it waits for the next accept. */
  async acceptLessons(dispatchId: string, digests: string[]): Promise<SwarmOutLessons> {
    const { data } = await api.post(`/swarm-out/runs/${encodeURIComponent(dispatchId)}/lessons/accept`,
      { digests });
    return data as SwarmOutLessons;
  },
  async returnReject(dispatchId: string, key: string, tip: string): Promise<void> {
    await api.post(`/swarm-out/runs/${encodeURIComponent(dispatchId)}/return/reject`, { key, tip });
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

  async answer(ask: SwarmOutAsk, body: { answers?: Record<string, string>; decision?: 'approve' | 'deny' }): Promise<void> {
    const seg = [ask.hive_id, ask.session_id, ask.request_id].map(encodeURIComponent);
    const payload = ask.kind === 'approval' ? { ...body, shown_sha256: ask.shown_sha256 } : body;
    await api.post(`/swarm-out/asks/${seg[0]}/${seg[1]}/${seg[2]}/answer`, payload);
  },
};
