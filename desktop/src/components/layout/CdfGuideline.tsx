/**
 * How it works — how the Cognitive Distillation Flow turns work into judgment (Need You →
 * How it works; layout Knowledge/Mockups/2026-11-24-needs-you-redesign.html). Written from
 * the running code: sources only enqueue; one worker decides each unit in five steps
 * (basic checks, pick the place, trust floor, compare, write —
 * core/ingestion/pipeline_v2.run_unit_v2); the producing agent decides meaning, code
 * checks facts (docs/design/2026-10-30-cdf-agent-decides.md); routing by kind and scope
 * (core/ingestion/contracts_v2.route_unit).
 *
 * The diagram carries the last 7 days' numbers (GET /ingestion/landed, the Cognition flow
 * tab's own query): how much each source sent, what each step dropped or moved, how much
 * reached each place. Below it, one line each: the four kinds it keeps, where each goes,
 * four rules. Bilingual inline like the Eval guide: the text belongs next to the figure.
 *
 * Key export: `CdfGuideline`.
 */
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ingestionService, type LandedOverview } from '../../services/ingestion';
import { ZoneLabel } from './needYouParts';
import { fmt, placeTotals } from './cdfText';

type Say = (en: string, zh: string) => string;

function useSay(): Say {
  const { i18n } = useTranslation();
  const zh = (i18n?.language ?? 'en').toLowerCase().startsWith('zh');
  return (en, zh_) => (zh ? zh_ : en);
}

const CARD = 'var(--color-card)';
const LINE = 'var(--color-border)';
const TEXT = 'var(--color-text)';
const MUTED = 'var(--color-text-muted)';
const DIM = 'var(--color-text-dim)';
const KEPT = 'var(--color-status-success)';
const MOVE = 'var(--color-zone-cognitive)';

const SOURCES = ['S1', 'S7', 'S2', 'S4', 'S3', 'S6'] as const;

function FlowDiagram({ data }: { data?: LandedOverview }) {
  const say = useSay();
  const n = (v: number | undefined) => (data && v != null ? fmt(v) : '');
  const srcName: Record<string, string> = {
    S1: say('You said it', '你说的'), S7: say('Post-session review', '会后复盘'), S2: say('Session', '会话'),
    S4: say('Code changes', '代码变更'), S3: say('Pipeline run', 'Pipeline run'), S6: say('Hive & signals', 'Hive 和外部信号'),
  };
  // a source that sent nothing this week is left out once the numbers are known
  const sources = SOURCES.filter((s) => !data || (data.sources[s] ?? 0) > 0);
  const s = data?.steps;
  const steps: Array<[string, string, string]> = [
    [say('Basic checks', '基础检查'), data ? say(`dropped ${n(s?.basicDropped)}`, `丢掉 ${n(s?.basicDropped)}`) : say('facts only', '只看事实'), DIM],
    [say('Pick the place', '定位置'), data ? say(`no place ${n(s?.placeDropped)}`, `没地方放 ${n(s?.placeDropped)}`) : say('kind × who it is about', '类型 × 关于谁'), DIM],
    [say('Trust floor', '信任底线'), data ? say(`moved to a brain ${n(s?.moved)}`, `改投到大脑 ${n(s?.moved)}`) : say('every-turn files: your words or a measurement', '每轮文件：你的原话或实测'), MOVE],
    [say('Compare with what it knows', '和已有的比'), data ? say(`same ${n(s?.reinforced)} · known ${n(s?.known)}`, `相同 ${n(s?.reinforced)} · 已有 ${n(s?.known)}`) : say('new · adds to · same as', '新的 · 补充 · 相同'), DIM],
    [say('Write', '写入'), data ? say(`${n(s?.written)} entries`, `${n(s?.written)} 条`) : say('with its sources and links', '带上来源和关联'), KEPT],
  ];
  const totals = placeTotals(data);
  const tiers: Array<[string, string]> = [
    [say('Read every turn', '每轮都读'), say('Memory · Evolution · your rules · Timeline', 'Memory · Evolution · 你的规则 · 时间线')],
    [say('Project brains', '项目大脑'), 'PRODUCT · TECH · IMPROVEMENT · PROJECT'],
    [say('General Brain & Library', '公共大脑和 Library'), say('by subject · references', '按主题 · 参考资料')],
  ];
  const sy = (i: number) => 40 + i * Math.min(66, 330 / Math.max(1, sources.length));
  const ky = (i: number) => 40 + i * 80;
  const ty = (i: number) => 40 + i * 130;
  const lastSource = sources.length ? sy(sources.length - 1) + 50 : 90;
  return (
    <svg viewBox="0 0 1180 470" width="100%" role="img" data-testid="cdf-guide-diagram"
      aria-label={say('Cognitive Distillation Flow diagram', '认知蒸馏流程图')}>
      <defs>
        <marker id="cdf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" fill={DIM} />
        </marker>
        <marker id="cdf-arrow-kept" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" fill={KEPT} />
        </marker>
      </defs>
      {([[0, say('WHERE IT COMES FROM', '从哪来')], [430, say('ONE WORKER DECIDES EACH ITEM', '逐条判断')], [880, say('WHERE IT LANDS', '落到哪里')]] as const)
        .map(([x, label]) => <text key={x} x={x} y={18} fill={MUTED} fontSize={12} fontWeight={600} letterSpacing={1}>{label}</text>)}
      {sources.map((key, i) => (
        <g key={key}>
          <rect x={0} y={sy(i)} width={210} height={50} rx={10} fill={CARD} stroke={LINE} />
          <text x={14} y={sy(i) + 30} fill={TEXT} fontSize={15} fontWeight={600}>{srcName[key]}</text>
          <text x={196} y={sy(i) + 30} fill={MUTED} fontSize={14} textAnchor="end">{n(data?.sources[key])}</text>
          <path d={`M210,${sy(i) + 25} C260,${sy(i) + 25} 260,194 300,194`} fill="none" stroke={DIM} />
        </g>
      ))}
      <rect x={300} y={160} width={96} height={68} rx={34} fill={CARD} stroke={LINE} />
      <text x={348} y={data ? 190 : 199} fill={TEXT} fontSize={14} fontWeight={600} textAnchor="middle">{say('Queue', '待写入')}</text>
      {data && <text x={348} y={210} fill={MUTED} fontSize={12} textAnchor="middle">{fmt(data.total)}</text>}
      <path d="M396,194 L430,194" stroke={DIM} markerEnd="url(#cdf-arrow)" />
      {steps.map(([title, sub, tone], i) => (
        <g key={title}>
          <rect x={430} y={ky(i)} width={290} height={58} rx={10} fill={CARD} stroke={i === steps.length - 1 ? KEPT : LINE} />
          <text x={446} y={ky(i) + 25} fill={TEXT} fontSize={15} fontWeight={600}>{title}</text>
          <text x={446} y={ky(i) + 46} fill={tone} fontSize={13}>{sub}</text>
          {i < steps.length - 1 && <path d={`M575,${ky(i) + 58} L575,${ky(i + 1)}`} stroke={DIM} markerEnd="url(#cdf-arrow)" />}
        </g>
      ))}
      <path d={`M720,${ky(2) + 29} C800,${ky(2) + 29} 800,${ty(1) + 24} 880,${ty(1) + 24}`} fill="none" stroke={MOVE} strokeDasharray="4 4" markerEnd="url(#cdf-arrow)" />
      {tiers.map(([title, sub], i) => (
        <g key={title}>
          <rect x={880} y={ty(i)} width={300} height={104} rx={10} fill={CARD} stroke={LINE} />
          <text x={896} y={ty(i) + 28} fill={TEXT} fontSize={15} fontWeight={600}>{title}</text>
          <text x={896} y={ty(i) + 48} fill={MUTED} fontSize={12}>{sub}</text>
          {totals && <text x={896} y={ty(i) + 84} fill={KEPT} fontSize={24} fontWeight={600}>{fmt(totals[i])}</text>}
          <path d={`M720,${ky(4) + 29} C820,${ky(4) + 29} 820,${ty(i) + 60} 880,${ty(i) + 60}`} fill="none" stroke={KEPT} markerEnd="url(#cdf-arrow-kept)" />
        </g>
      ))}
      <path d={`M1030,${ty(2) + 104} L1030,452 L105,452 L105,${lastSource + 6}`} fill="none" stroke={KEPT} strokeDasharray="2 5" markerEnd="url(#cdf-arrow-kept)" />
      <text x={575} y={444} fill={TEXT} fontSize={13} fontWeight={600} textAnchor="middle">{say('The next session starts with it', '下一个会话带着它开始')}</text>
    </svg>
  );
}

export function CdfGuideline() {
  const say = useSay();
  // the Cognition flow tab's own 7-day query: reused while fresh, not fetched again on a tab switch
  const landed = useQuery({ queryKey: ['ingestion', 'landed', '7d'], queryFn: () => ingestionService.landed('7d'), staleTime: 60_000 });
  const kinds: Array<[string, string]> = [
    [say('Lesson', '经验'), say('Principles, corrections, decisions, practices, pitfalls. The only kind that compounds.', '原则、纠正、决定、做法、坑。唯一会复利的一类。')],
    [say('Gap', '缺口'), say('A weak point still open; closed when an event says it is done.', '还没解决的薄弱点，有事件说做完了才关闭。')],
    [say('Event', '事件'), say('What happened and what it changed, summarised by month.', '发生了什么、改变了什么，按月汇总。')],
    [say('Reference', '参考'), say('A document or link worth keeping; recalled, never treated as judgment.', '值得留的文档和链接，能召回，不当判断。')],
  ];
  const places: Array<[string, string]> = [
    [say('About you', '关于你'), say('preference → Steering · profile → User · environment → Tools', '偏好 → Steering · 画像 → User · 环境 → Tools')],
    [say('About the agent', '关于 agent'), say('a correction or a competence → Evolution', '纠正、能力 → Evolution')],
    [say('True anywhere', '处处成立'), 'Memory'],
    [say('About a project', '关于某个项目'), say('decision → PROJECT · product → PRODUCT · building → TECH · lessons and gaps → IMPROVEMENT', '决定 → PROJECT · 产品 → PRODUCT · 构建 → TECH · 经验和缺口 → IMPROVEMENT')],
    [say('No such project', '项目不存在'), say('the General Brain, by subject', '公共大脑，按主题')],
    [say('An event · a reference', '事件 · 参考'), say('Timeline · Library', '时间线 · Library')],
  ];
  const rules = [
    say('The agent that was there decides the meaning; code only checks facts.', '在场的 agent 判断含义，代码只核对事实。'),
    say('Files read every turn take only your words or a measurement; the rest goes to a brain, never dropped.', '每轮都读的文件只收你的原话或实测，其余改投到大脑，不丢。'),
    say('The same thing again is one more confirmation; a replaced entry is archived before it leaves.', '相同的多记一次确认；被替换的先归档再移走。'),
    say('Something wrong? Say “forget that” in chat; what you said in the last 24 hours can be removed.', '不对就在对话里说「忘掉这条」；24 小时内你说的话可以删掉。'),
  ];
  return (
    <div className="space-y-8 py-2" data-testid="cdf-guide">
      <section className="so-card overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-[18px] py-4">
        <FlowDiagram data={landed.data} />
      </section>
      <section>
        <ZoneLabel>{say('Four kinds of things it keeps', '它留下四类东西')}</ZoneLabel>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {kinds.map(([title, body]) => (
            <div key={title} className="rounded-[10px] bg-[var(--color-bg-chrome)] px-3 py-2.5">
              <div className="text-body font-semibold">{title}</div>
              <div className="text-small text-[var(--color-text-muted)]">{body}</div>
            </div>
          ))}
        </div>
      </section>
      <div className="grid grid-cols-1 gap-x-10 gap-y-7 md:grid-cols-2">
        <section>
          <ZoneLabel>{say('Who it is about decides where it goes', '关于谁，就放到哪')}</ZoneLabel>
          {places.map(([who, where]) => (
            <div key={who} className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 border-t border-[var(--color-border)] py-1.5 text-small">
              <b className="font-semibold">{who}</b><span className="text-[var(--color-text-secondary)]">{where}</span>
            </div>
          ))}
        </section>
        <section>
          <ZoneLabel>{say('A few rules', '几条规矩')}</ZoneLabel>
          {rules.map((rule, i) => (
            <div key={rule} className="grid grid-cols-[18px_minmax(0,1fr)] gap-x-2.5 py-1.5 text-body">
              <span className="font-semibold" style={{ color: KEPT }}>{i + 1}</span>
              <span className="text-[var(--color-text-secondary)]">{rule}</span>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}
