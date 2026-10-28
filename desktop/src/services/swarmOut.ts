/**
 * Swarm Out service: tasks dispatched to Hives and the questions they wait on.
 *
 * Backend: /api/swarm-out (backend/routers/swarm_out.py). Hive credentials never
 * reach the webview; every call goes through the local daemon.
 */
import api from './api';

export interface SwarmOutRun {
  dispatch_id: string;
  hive_id: string;
  hive: string;
  session_id: string;
  title: string;
  origin: string;
  sent_at: string;
  waiting: boolean;
  /** Its session is streaming on the Hive now; null when that Hive could not be asked. */
  running: boolean | null;
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
    await api.post(`/swarm-out/asks/${seg[0]}/${seg[1]}/${seg[2]}/answer`, body);
  },
};
