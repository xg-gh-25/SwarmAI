/**
 * Guideline — how the Cognitive Distillation Flow turns work into judgment, with its flow
 * diagram (Need You → How it works). Written from the running code, not the first
 * design: sources only enqueue; one worker decides each unit in five steps (basic checks,
 * pick the place, trust floor, compare, write — core/ingestion/pipeline_v2.run_unit_v2);
 * the producing agent decides meaning, code checks facts (docs/design/2026-10-30-cdf-
 * agent-decides.md); routing by kind and scope (core/ingestion/contracts_v2.route_unit).
 *
 * Mechanisms only — no counts or dates (the Cognition flow tab shows the live ones).
 * Bilingual inline like the Eval guide: the prose is long and belongs next to the figure.
 *
 * Key export: `CdfGuideline`.
 */
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { SectionLabel } from '../overlay';

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
const KEPT = 'var(--color-zone-cognitive)';
const MOVE = 'var(--color-primary)';
const WARN = 'var(--color-status-warning)';

function Box({ x, y, w, h, title, sub, accent }: {
  x: number; y: number; w: number; h: number; title: string; sub?: string; accent?: string;
}) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={8} fill={CARD} stroke={accent ?? LINE} />
      <text x={x + 12} y={y + (sub ? 22 : h / 2 + 4)} fill={TEXT} fontSize={13} fontWeight={600}>{title}</text>
      {sub && <text x={x + 12} y={y + 40} fill={MUTED} fontSize={11}>{sub}</text>}
    </g>
  );
}

function FlowDiagram() {
  const say = useSay();
  const sources = [
    [say('You said it', '你说的'), say('a correction, “remember this”', '纠正、“记住这个”')],
    [say('Post-session review', '会后复盘'), say('a fork of the session reads it back', 'fork 出的会话回看全程')],
    [say('Session', '会话'), say('lessons and events while working', '工作中的经验和事件')],
    [say('Pipeline run', 'Pipeline run'), say('the run’s REFLECT report', 'run 的 REFLECT 报告')],
    [say('Hive & signals', 'Hive 和外部信号'), say('Hive lessons, outside references', 'Hive 教训、外部参考资料')],
    [say('Code changes · metrics', '代码变更 · 系统度量'), say('scheduled', '定时')],
  ];
  const steps = [
    [say('1  Basic checks', '1  基础检查'), say('facts only: empty, a password in a link', '只看事实：空内容、链接带密码')],
    [say('2  Pick the place', '2  定位置'), say('kind × who it is about → file › section', '类型 × 关于谁 → 文件 › 小节')],
    [say('3  Trust floor', '3  信任底线'), say('every-turn files: your words or a measurement', '每轮文件：你的原话或实测')],
    [say('4  Compare with what it knows', '4  和已有内容比对'), say('the agent declared: new · adds to · same as', 'agent 声明：新的 · 补充 · 相同')],
    [say('5  Write', '5  写入'), say('with its sources and its links', '带上来源和关联')],
  ];
  const sy = (i: number) => 44 + i * 64;          // source box top
  const ky = (i: number) => 44 + i * 72;          // step box top
  const tiers = [
    { y: 44, h: 130, title: say('Read every turn', '每轮都读'), sub: say('in every prompt', '进每轮 prompt'),
      items: [say('Memory · Evolution', 'Memory · Evolution'), say('Steering · User · Tools', 'Steering · User · Tools'), say('Timeline', '时间线')] },
    { y: 190, h: 104, title: say('Project brains', '项目大脑'), sub: say('recalled when working on it', '做这个项目时召回'),
      items: [say('PRODUCT · TECH', 'PRODUCT · TECH'), say('IMPROVEMENT · PROJECT', 'IMPROVEMENT · PROJECT')] },
    { y: 310, h: 84, title: say('General Brain & Library', '公共大脑和 Library'), sub: say('recalled everywhere', '所有地方都能召回'),
      items: [say('by subject · references', '按主题 · 参考资料')] },
  ];
  const DX = 900, DW = 280, TRUNK = 872;
  return (
    <svg viewBox="0 0 1180 520" width="100%" role="img" data-testid="cdf-guide-diagram"
      aria-label={say('Cognitive Distillation Flow diagram', '认知蒸馏流程图')}>
      <defs>
        <marker id="cdf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill={DIM} />
        </marker>
        <marker id="cdf-arrow-kept" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" fill={KEPT} />
        </marker>
        <marker id="cdf-arrow-move" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" fill={MOVE} />
        </marker>
      </defs>

      {[[0, say('WHERE IT COMES FROM', '从哪来')], [244, say('INBOX', 'INBOX')], [430, say('ONE WORKER DECIDES EACH ITEM', '一个 worker 逐条判断')], [DX, say('WHERE IT LANDS', '落到哪里')]]
        .map(([x, label]) => <text key={String(x)} x={Number(x)} y={24} fill={MUTED} fontSize={11} fontWeight={600} letterSpacing={1.2}>{label}</text>)}

      {sources.map(([title, sub], i) => (
        <g key={title}>
          <Box x={0} y={sy(i)} w={196} h={52} title={title} sub={sub} />
          <path d={`M196,${sy(i) + 26} C222,${sy(i) + 26} 222,236 244,236`} fill="none" stroke={DIM} />
        </g>
      ))}

      <g>
        <ellipse cx={314} cy={164} rx={70} ry={14} fill={CARD} stroke={LINE} />
        <path d="M244,164 L244,306 A70,14 0 0 0 384,306 L384,164" fill={CARD} stroke={LINE} />
        <ellipse cx={314} cy={164} rx={70} ry={14} fill={CARD} stroke={LINE} />
        <text x={314} y={206} fill={TEXT} fontSize={13} fontWeight={600} textAnchor="middle">ingestion.db</text>
        <text x={314} y={228} fill={MUTED} fontSize={11} textAnchor="middle">{say('a source only enqueues', 'source 只负责入队')}</text>
        <text x={314} y={246} fill={MUTED} fontSize={11} textAnchor="middle">{say('one insert, never waits', '一次插入，不等待')}</text>
        <text x={314} y={264} fill={MUTED} fontSize={11} textAnchor="middle">{say('on chat; kept 30 days', '不阻塞 chat；保留 30 天')}</text>
      </g>
      <path d={`M384,236 C408,236 408,${ky(0) + 29} 430,${ky(0) + 29}`} fill="none" stroke={DIM} markerEnd="url(#cdf-arrow)" />

      {steps.map(([title, sub], i) => (
        <g key={title}>
          <Box x={430} y={ky(i)} w={270} h={58} title={title} sub={sub} accent={i === 4 ? KEPT : undefined} />
          {i < steps.length - 1 && <path d={`M565,${ky(i) + 58} L565,${ky(i + 1)}`} stroke={DIM} markerEnd="url(#cdf-arrow)" />}
        </g>
      ))}
      {/* side exits: labels only, except the trust floor's move (a dashed line into the project brains) */}
      <text x={712} y={ky(0) + 33} fill={DIM} fontSize={11}>{say('→ dropped', '→ 丢掉')}</text>
      <text x={712} y={ky(1) + 25} fill={WARN} fontSize={11}>{say('no such project', '项目不存在')}</text>
      <text x={712} y={ky(1) + 41} fill={WARN} fontSize={11}>{say('→ General Brain', '→ 公共大脑')}</text>
      <text x={712} y={ky(2) + 22} fill={MOVE} fontSize={11}>{say('not your words', '不是你的原话')}</text>
      <path d={`M700,${ky(2) + 29} L${DX},${ky(2) + 29}`} fill="none" stroke={MOVE} strokeDasharray="4 4" markerEnd="url(#cdf-arrow-move)" />
      <text x={712} y={ky(3) + 25} fill={KEPT} fontSize={11}>{say('same → +1 confirmation', '相同 → +1 次确认')}</text>
      <text x={712} y={ky(3) + 41} fill={DIM} fontSize={11}>{say('nothing new → skipped', '没有新证据 → 跳过')}</text>
      {/* what is written: one trunk, one branch into each place */}
      <path d={`M700,${ky(4) + 29} L${TRUNK},${ky(4) + 29} L${TRUNK},${tiers[0].y + 24}`} fill="none" stroke={KEPT} />
      {tiers.map((tier) => (
        <path key={tier.title} d={`M${TRUNK},${tier.y + 24} L${DX},${tier.y + 24}`} fill="none" stroke={KEPT} markerEnd="url(#cdf-arrow-kept)" />
      ))}

      {tiers.map((tier) => (
        <g key={tier.title}>
          <rect x={DX} y={tier.y} width={DW} height={tier.h} rx={8} fill={CARD} stroke={LINE} />
          <text x={DX + 14} y={tier.y + 24} fill={TEXT} fontSize={13} fontWeight={600}>{tier.title}</text>
          <text x={DX + 14} y={tier.y + 42} fill={DIM} fontSize={11}>{tier.sub}</text>
          {tier.items.map((item, index) => (
            <text key={item} x={DX + 14} y={tier.y + 64 + index * 19} fill={MUTED} fontSize={12}>{item}</text>
          ))}
        </g>
      ))}

      {/* the loop: what lands is read back by the next session */}
      <path d={`M${DX + DW / 2},394 L${DX + DW / 2},456 L98,456 L98,${sy(5) + 52 + 4}`} fill="none" stroke={KEPT} strokeDasharray="2 5" markerEnd="url(#cdf-arrow-kept)" />
      <text x={540} y={482} fill={TEXT} fontSize={12} fontWeight={600} textAnchor="middle">
        {say('The next session starts with it', '下一个会话带着它开始')}
      </text>
      <text x={540} y={502} fill={MUTED} fontSize={11} textAnchor="middle">
        {say('every-turn files are in the prompt, brains are recalled when relevant → better judgment → better lessons. That is how it compounds.',
          '每轮文件在 prompt 里，大脑在相关时召回 → 判断更好 → 沉淀更好的经验。这就是复利。')}
      </text>
    </svg>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <SectionLabel>{title}</SectionLabel>
      <div className="mt-2.5">{children}</div>
    </section>
  );
}

function Row({ left, right }: { left: string; right: ReactNode }) {
  return (
    <div className="grid grid-cols-[220px_1fr] gap-4 border-t border-[var(--color-border)] py-2 text-body">
      <span className="text-[var(--color-text)]">{left}</span>
      <span className="text-[var(--color-text-muted)]">{right}</span>
    </div>
  );
}

export function CdfGuideline() {
  const say = useSay();
  const kinds = [
    [say('Lesson', '经验'), say('A piece of judgment: principle, correction, decision, model, guideline, pitfall or process. The only kind that compounds.',
      '一条判断：原则、纠正、决定、心智模型、做法、坑或流程。唯一会产生复利的一类。')],
    [say('Gap', '缺口'), say('Something still open: debt, a risk, a deferred finding, a process issue, a follow-up. Closed when an event says it is done.',
      '还没解决的事：技术债、风险、推迟的发现、流程问题、待跟进。等有事件说明做完了才关闭。')],
    [say('Event', '事件'), say('What happened and what it changed. Kept as history, summarised by month, read for retros; a later review of the same session replaces one it wrote earlier that no longer holds.',
      '发生了什么、带来了什么影响。作为历史保留，按月汇总，供复盘用；同一会话后来的复盘会替换它自己先前写下、已经不成立的那条。')],
    [say('Reference', '参考资料'), say('A document or a link worth keeping. Recalled, never treated as judgment.',
      '值得留下的文档或链接。可以召回，但不当作判断。')],
  ];
  return (
    <div className="space-y-9 pt-6" data-testid="cdf-guide">
      <div className="max-w-[900px]">
        <h2 className="text-title font-semibold text-[var(--color-text)]">{say('How SwarmAI turns work into judgment', 'SwarmAI 怎样把工作沉淀成判断力')}</h2>
        <p className="mt-2 text-body text-[var(--color-text-muted)]">
          {say('The Cognitive Distillation Flow (CDF) is the engine behind SwarmAI’s memory. Whatever is worth keeping from a session, a run, your corrections or an outside source is written down by the agent that was there, then decided item by item and filed in the one place it belongs. Everything below happens on its own, and every step is recorded — the Cognition flow tab shows each item’s path.',
            '认知蒸馏流（CDF）是 SwarmAI 记忆背后的引擎。会话、run、你的纠正和外部资料里值得保留的内容，由在场的 agent 写下来，再逐条判断、放进唯一该放的位置。下面这些都自动进行，每一步都有记录；在“认知沉淀”里能看到每一条走过的路径。')}
        </p>
      </div>

      <FlowDiagram />

      <Block title={say('Four kinds of things it keeps', '它保留的四类东西')}>
        <div className="grid grid-cols-4 gap-6">
          {kinds.map(([title, body]) => (
            <div key={title}>
              <div className="text-body font-semibold text-[var(--color-text)]">{title}</div>
              <p className="mt-1 text-small text-[var(--color-text-muted)]">{body}</p>
            </div>
          ))}
        </div>
      </Block>

      <Block title={say('Who it is about decides where it goes', '关于谁，决定放到哪')}>
        <Row left={say('About you', '关于你')} right={say('your preference → Steering · your profile → User · your environment → Tools', '你的偏好 → Steering · 你的画像 → User · 你的环境 → Tools')} />
        <Row left={say('About the agent', '关于 agent')} right={say('a correction or a competence → Evolution', '纠正或能力 → Evolution')} />
        <Row left={say('About the world', '关于世界')} right={say('true anywhere → Memory, in the section of its type', '放到哪都成立 → Memory，按类型进对应小节')} />
        <Row left={say('About a project', '关于某个项目')} right={say('a decision → PROJECT · product → PRODUCT · how it is built → TECH · a lesson learned building it, or a gap → IMPROVEMENT',
          '决定 → PROJECT · 产品 → PRODUCT · 怎么构建 → TECH · 构建中学到的经验或缺口 → IMPROVEMENT')} />
        <Row left={say('A project that does not exist', '项目不存在')} right={say('the General Brain keeps it under the subject it named', '由公共大脑按它提到的主题保存')} />
        <Row left={say('An event · a reference', '事件 · 参考资料')} right={say('Timeline · Knowledge Library', '时间线 · Knowledge Library')} />
      </Block>

      <div className="grid grid-cols-2 gap-10">
        <Block title={say('Who decides what', '谁判断什么')}>
          <div className="text-body font-semibold text-[var(--color-text)]">{say('The agent that was there decides the meaning', '在场的 agent 判断含义')}</div>
          <p className="mt-1 text-small text-[var(--color-text-muted)]">
            {say('Is it worth keeping, what kind, who it is about, whether it is new, adds to an entry or is the same as one, what it replaces, whether a correction repeats an old one. It has the whole session in view.',
              '值不值得留、是哪一类、关于谁、是新的还是补充或等同某条、它替换了什么、这次纠正是不是旧错重犯。它看得到整个会话。')}
          </p>
          <div className="mt-3 text-body font-semibold text-[var(--color-text)]">{say('Code checks only facts', '代码只核对事实')}</div>
          <p className="mt-1 text-small text-[var(--color-text-muted)]">
            {say('Whose words are quoted, whether outside content is cited, secrets and injected text, an exact restatement, whether the section exists. A weaker judge — a pattern, a keyword match, a model that saw less — never overrules the agent.',
              '引用的是谁的话、有没有引用外部内容、密钥和注入、是否逐字重复、目标小节是否存在。更弱的判断者（正则、关键词匹配、看到更少上下文的模型）不能推翻 agent 的判断。')}
          </p>
        </Block>
        <Block title={say('The trust floor', '信任底线')}>
          <p className="text-small text-[var(--color-text-muted)]">
            {say('Files read in every turn shape every answer, so only what you said or what was measured may enter them:',
              '每轮都读的文件会影响每一次回答，所以只有你说过的话或实测出来的结果才能进去：')}
          </p>
          <ul className="mt-2 space-y-1.5 text-small text-[var(--color-text-muted)]">
            <li><span className="text-[var(--color-text)]">Steering · User · Tools</span> — {say('a verbatim quote of yours, then checked by a model that sees only your messages', '要有你的逐字原话，再由一个只看得到你消息的模型核对')}</li>
            <li><span className="text-[var(--color-text)]">Memory · Evolution</span> — {say('your words or a measurement from the session or run', '你的原话，或会话 / run 里的实测数据')}</li>
            <li><span className="text-[var(--color-text)]">{say('Anything else', '其余的')}</span> — {say('kept in the project brain or the General Brain and recalled when relevant. Moved, never dropped.', '放进项目大脑或公共大脑，相关时召回。只改投，不丢弃。')}</li>
          </ul>
        </Block>
      </div>

      <div className="grid grid-cols-2 gap-10">
        <Block title={say('How it compounds', '怎么产生复利')}>
          <ul className="space-y-1.5 text-small text-[var(--color-text-muted)]">
            <li><span className="text-[var(--color-text)]">{say('Same', '相同')}</span> — {say('the entry gets one more independent confirmation; nothing is written twice', '那一条多一次独立确认，不重复写')}</li>
            <li><span className="text-[var(--color-text)]">{say('Adds to', '补充')}</span> — {say('written and linked to the entry it builds on', '写入，并关联到它所补充的那一条')}</li>
            <li><span className="text-[var(--color-text)]">{say('Replaces', '替换')}</span> — {say('the old entry is archived first, then removed; the main files hold only what is in force', '旧条目先归档再移除；主文件里只保留当前有效的内容')}</li>
            <li><span className="text-[var(--color-text)]">{say('Withdrawn by you', '你撤回的')}</span> — {say('stays out: saying it again is refused', '不再进来：再次出现会被拒')}</li>
          </ul>
        </Block>
        <Block title={say('What you can do', '你可以做什么')}>
          <ul className="space-y-1.5 text-small text-[var(--color-text-muted)]">
            <li>{say('Read any item’s path in Cognition flow — where it landed, why there, what it was compared with.', '在“认知沉淀”里看任何一条的路径：落在哪、为什么在那、和什么比对过。')}</li>
            <li>{say('Tell Swarm in chat when something is wrong (“forget that”, “that is not what I said”). There are no approval buttons here: the fix goes into the rules, not into one item.', '发现不对就在 chat 里告诉 Swarm（“忘掉这条”、“我不是这个意思”）。这里没有审批按钮：问题要修在规则上，不是改某一条。')}</li>
            <li>{say('Something you said in the last 24 hours can be redacted from its row.', '最近 24 小时内你说过的话，可以在对应条目里删除。')}</li>
          </ul>
        </Block>
      </div>
    </div>
  );
}
