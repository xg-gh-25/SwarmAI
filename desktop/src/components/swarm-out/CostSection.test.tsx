/**
 * CostSection (mock v7, the cost section): (1) "with machine" by default, a verdict, the
 * EC2 busy share and the estimate note; (2) switching to "model only" changes the same
 * table and the facts; (3) a kind of work opens to its tokens per accepted run; (4) with
 * too few AgentCore runs it says it cannot compare yet; (5) an older backend with no
 * machine records says so.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import type { SwarmOutMachines, SwarmOutRun } from '../../services/swarmOut';
import { CostSection } from './CostSection';
import i18n from '../../i18n';

beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

const T = Date.parse('2026-11-23T12:00:00Z');
const h = (n: number) => new Date(T - n * 3600_000).toISOString();
let seq = 0;
const run = (ac: boolean, cost: number): SwarmOutRun => {
  seq += 1;
  return {
    dispatch_id: `so_${String(seq).padStart(12, '0')}`, hive_id: ac ? 'el' : 'h1', hive: ac ? 'elastic' : 'demo',
    session_id: `s${seq}`, title: `t${seq}`, origin: 'swarm-out', sent_at: h(1), state: 'stopped', state_reason: 'idle',
    stage: 'accepted', rounds: [{ carrier: ac ? 'agentcore' : 'ec2' }],
    project_agent: { brain: 'SwarmAI', mode: 'code-change', revision: 'r', profile: 'p', acceptance: 'a', execution: 'native' },
    ...(ac ? { machine: { seconds: 600, usd: 0.1 } } : {}),
    result: { active_s: 600, cost_usd: cost, tokens: { input: 1000, output: 20000, cache_read: 900000, cache_write: 10000 } } as SwarmOutRun['result'],
  } as SwarmOutRun;
};
const MACHINES: SwarmOutMachines = {
  ec2: [{ hive_id: 'h1', name: 'demo', instance_type: 'm7g.xlarge', hourly_usd: 0.163, recorded_since: h(10), on: [[h(10), h(0)]] }],
  agentcore: { vcpu: 2, memory_gib: 8, idle_timeout_s: 900, usd_per_vcpu_hour: 0.0895, usd_per_gib_hour: 0.00945 }, estimate: true,
};
const five = (ac: boolean, cost: number) => Array.from({ length: 5 }, () => run(ac, cost));

describe('CostSection', () => {
  it('(1)(2)(3) with machine by default, then model only; a kind opens to its tokens', () => {
    render(<CostSection runs={[...five(false, 4), ...five(true, 3)]} machines={MACHINES} from={T - 86400_000} to={T} grown compact={false} />);
    const box = screen.getByTestId('history-cost');
    expect(box).toHaveAttribute('data-layer', 'all');
    expect(screen.getByTestId('cost-verdict')).toHaveTextContent('AgentCore 省');
    expect(screen.getByTestId('cost-busy')).toHaveTextContent('常驻 Hive 忙碌');
    expect(screen.getByTestId('cost-note')).toHaveTextContent('机器费是估算');
    expect(screen.getByTestId('cost-note')).toHaveTextContent('EC2 开机记录从');
    const sides = screen.getAllByTestId('cost-side');
    expect(sides.map((s) => s.getAttribute('data-carrier'))).toEqual(['ec2', 'agentcore']);
    expect(within(sides[1]).getByTestId('cost-per-accepted')).toHaveAttribute('data-low', 'true');

    fireEvent.click(within(screen.getByTestId('cost-layer')).getByText('只看模型'));
    expect(box).toHaveAttribute('data-layer', 'model');
    expect(screen.getByTestId('cost-verdict')).toHaveTextContent('模型费 AgentCore 低 25%');
    expect(screen.getByTestId('cost-facts')).toHaveTextContent('缓存命中');
    expect(screen.queryByTestId('cost-busy')).toBeNull();
    expect(screen.getByTestId('cost-note')).toHaveTextContent('这一层不用来判断哪边省钱');

    expect(screen.queryByTestId('cost-tokens')).toBeNull();
    fireEvent.click(within(screen.getByTestId('cost-kind')).getAllByTestId('cost-side')[0]);
    const tok = screen.getByTestId('cost-tokens');
    expect(tok).toHaveTextContent('缓存读');
    expect(tok).toHaveTextContent('900K');
  });

  it('(4) too few AgentCore runs: cannot compare yet, and its row says too few', () => {
    render(<CostSection runs={[...five(false, 4), run(true, 3)]} machines={MACHINES} from={T - 86400_000} to={T} grown compact={false} />);
    expect(screen.getByTestId('cost-verdict')).toHaveTextContent('还比不出来');
    const ac = screen.getAllByTestId('cost-side').find((s) => s.getAttribute('data-carrier') === 'agentcore')!;
    expect(ac).toHaveTextContent('样本不够');
  });

  it('(5) an older backend with no machines says machine cost is not recorded', () => {
    render(<CostSection runs={five(false, 4)} machines={undefined} from={T - 86400_000} to={T} grown compact />);
    expect(screen.getByTestId('cost-note')).toHaveTextContent('后端还没有记录机器费');
    expect(screen.getByTestId('cost-note')).toHaveTextContent('5 个任务没有机器费记录');
  });
});
