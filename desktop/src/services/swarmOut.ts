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

export interface SwarmOutHives {
  hives: SwarmOutHive[];
  running: number;
}

export const swarmOutService = {
  /** Names and status only: the panel never sees a Hive's address or credentials. */
  async hives(): Promise<SwarmOutHives> {
    const { data } = await api.get('/swarm-out/hives');
    return data as SwarmOutHives;
  },

  async runs(): Promise<SwarmOutRuns> {
    const { data } = await api.get('/swarm-out/runs');
    return data as SwarmOutRuns;
  },

  async answer(ask: SwarmOutAsk, body: { answers?: Record<string, string>; decision?: 'approve' | 'deny' }): Promise<void> {
    const seg = [ask.hive_id, ask.session_id, ask.request_id].map(encodeURIComponent);
    const payload = ask.kind === 'approval' ? { ...body, shown_sha256: ask.shown_sha256 } : body;
    await api.post(`/swarm-out/asks/${seg[0]}/${seg[1]}/${seg[2]}/answer`, payload);
  },
};
