/**
 * Guide — teaches the Eval-First method (Agent-DLC: an evaluation-driven agent
 * development lifecycle) and marks, in every section, where it lives in SwarmAI.
 *
 * Mechanisms only: no count, date, time or weekday is written here (those go stale;
 * the tabs show the live ones). Canon: Knowledge/Library eval-first knowledge base;
 * the Agent-DLC deck (pages 13–17) for the flywheel, five dimensions, tiers and checks.
 */
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';
import { LinkBtn, TierTag, useGo, useLang, type TabId } from './ui';

const SOURCES = [
  { en: 'AWS sample repository for Eval-First enterprise agents (with the four white papers)', zh: 'AWS 官方 Eval-First 示例仓库（含四篇白皮书全文）',
    url: 'https://github.com/aws-samples/sample-eval-first-building-enterprise-agents-with-agentcore' },
  { en: 'Amazon Bedrock AgentCore Evaluations', zh: 'Amazon Bedrock AgentCore Evaluations',
    url: 'https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html' },
  { en: 'THELMA: task-based holistic evaluation of LLM applications', zh: 'THELMA：面向任务的 LLM 应用整体评估', url: 'https://arxiv.org/abs/2505.11626' },
  { en: 'Mind the Goal: goal-level evaluation of multi-turn agents', zh: 'Mind the Goal：多轮 agent 的目标级评估', url: 'https://arxiv.org/abs/2510.03696' },
];

export function GuideTab() {
  const { t } = useLang();
  const go = useGo();
  const L = ({ to, anchor, children }: { to: TabId; anchor?: string; children: ReactNode }) => (
    <LinkBtn className="inline text-[var(--color-primary)]" onClick={() => go(to, anchor)}>{children}</LinkBtn>
  );
  const Here = ({ children }: { children: ReactNode }) => (
    <p className="text-small text-[var(--color-text)] mt-3 pl-3 border-l-2 border-[var(--color-brand)]">{children}</p>
  );
  const P = ({ children }: { children: ReactNode }) => <p className="text-body text-[var(--color-text)] mt-2 leading-relaxed">{children}</p>;
  const Two = ({ items }: { items: [string, string][] }) => (
    <div className="grid grid-cols-2 gap-x-8 gap-y-4 mt-3">
      {items.map(([a, b]) => <div key={a} className="text-small"><b className="block text-[var(--color-text)] mb-0.5">{a}</b><span className="text-[var(--color-text-muted)]">{b}</span></div>)}
    </div>
  );

  const sections: { id: string; title: string; body: ReactNode }[] = [
    { id: 'g-why', title: t('Why ordinary tests cannot hold an agent', '为什么传统测试测不住 agent'), body: <>
      <P>{t('An agent’s behaviour changes even when the code does not. Builds and unit tests look at code and cannot see that:', '代码没变，agent 的行为也会变。传统的 build 和单元测试看的是代码，看不见这种变化：')}</P>
      <ul className="list-disc pl-5 text-body text-[var(--color-text)] mt-2 space-y-1">
        <li><b>{t('Not deterministic: ', '不确定：')}</b>{t('the same input can get two different answers, even at temperature zero. "Assert it equals" stops working; you judge whether it is good.', '同一个输入，两次回答可能不同，温度设成零也一样。"断言等于某个值"失效，要换成"评估它好不好"。')}</li>
        <li><b>{t('The prompt is source code: ', 'prompt 就是源代码：')}</b>{t('changing a sentence of a prompt, a rule or a tool description changes the program, so every change gets evaluated.', '改一句 prompt、一条规则、一个工具描述，就是改了程序，所以每次都要评。')}</li>
        <li><b>{t('Dependencies drift on their own: ', '依赖会自己漂移：')}</b>{t('a model upgrade or a rebuilt index changes behaviour with nothing deployed, so evaluation runs continuously.', '模型升级、知识库重建，你什么都没部署，行为就变了，所以评估要持续跑。')}</li>
      </ul>
      <P>{t('And these failures raise no error: the error rate stays flat while the agent confidently answers wrong.', '更麻烦的是这些失败都不报错：错误率是平的，agent 只是自信地答错。')}</P>
      <p className="text-body italic text-[var(--color-text-muted)] mt-3">“Evaluation is the engineering work of building a trustworthy agent, not a QA step at the end.”</p>
    </> },
    { id: 'g-dlc', title: t('Agent-DLC: a six-step flywheel around one centre', 'Agent-DLC：六环飞轮，围着一个圆心转'), body: <>
      <div className="flex gap-8 items-start mt-3">
        <Wheel />
        <div className="space-y-2 text-small flex-1">
          {[
            [t('Define', '定义'), t('turn the goal into criteria per dimension and a golden test set', '把目标翻译成五维判据和黄金测试集'), <L to="cases">{t('Cases', '考题')}</L>],
            [t('Build', '构建'), t('prompts, tools, retrieval, memory: an agent that runs', '提示词、工具、检索、记忆，搭出能跑的 agent'), <span className="text-[var(--color-text-dim)]">{t('your everyday work', '你的日常开发')}</span>],
            [t('Evaluate', '评估'), t('score against the golden standard; attribute at session, trajectory and step level', '按黄金标准打分，会话 / 轨迹 / 步骤三级归因'), <L to="cases">{t('Cases', '考题')}</L>],
            [t('Release', '发布'), t('release only past the bar', '过线才放行'), <L to="overview">{t('Overview · four checks', 'Overview · 四道关')}</L>],
            [t('Observe', '观测'), t('sample and score real use; watch for silent drift', '对真实使用采样打分，盯住静默漂移'), <L to="online">{t('Live sessions', '真实会话')}</L>],
            [t('Improve', '回流'), t('failures go back into the golden standard, which gets harder each turn', '失败样本回到黄金标准，标准越滚越硬'), <L to="overview">{t('Overview · For you', '概览 · 要你处理')}</L>],
          ].map(([a, b, c], i) => <div key={i}><b className="text-[var(--color-text)] mr-2">{a}</b><span className="text-[var(--color-text-muted)]">{b}</span> · {c}</div>)}
        </div>
      </div>
      <P>{t('The centre is the golden standard: the golden set plus the criteria per dimension, your agreement on what "good" means. Define produces it, evaluate uses it, improve feeds it back.',
        '圆心是"黄金标准"：黄金测试集加五维判据，是你对"什么算好"的共识。定义产出它，评估取用它，回流回灌它。')}</P>
      <P>{t('There are two flywheels: the big one goes round all six steps and moves a version, slow and across a release; the small one sits between evaluate and release, the rework when a bar is missed: score, locate, change one thing, score again, until it passes.',
        '其实有两个飞轮：大飞轮是六环走一圈、升一个版本，慢，跨越上线；小飞轮只在"评估 → 发布"之间，是不过线时的返工：打分、定位、改一处、再打分，转到过线为止。')}</P>
      <Here>{t('In SwarmAI a person stays in the improvement loop: what the evaluation finds is listed in Overview under For you and never turns into a case or a rule change by itself. Generated cases were too poor; fewer, better cases win.',
        'SwarmAI 的回流有人在环里：评测发现的问题列在概览的“要你处理”里，不会自动变成 case 或改规则，去向由人决定。自动生成的 case 质量太差，宁缺毋滥。')}</Here>
    </> },
    { id: 'g-dims', title: t('Five dimensions: each asks one thing', '五个维度：每一维问一件事'), body: <>
      <Two items={[
        [t('Cognition · does it know your business', '认知 · 它真的懂你的业务吗'), t('terms, aliases, multi-hop relations, internal knowledge. SwarmAI: memory, recall, project docs, knowledge base.', '术语、别名、多跳关系、企业内部知识。SwarmAI：记忆、召回、项目文档、知识库。')],
        [t('Quality · right, complete, consistent', '质量 · 对不对、全不全、前后一致吗'), t('correctness, completeness, consistency, citable. SwarmAI: not agreeing with a wrong premise, delivered or not, how often you corrected it.', '正确性、完整性、一致性、可引用。SwarmAI：不附和错误前提、交付了没有、被你纠正了多少。')],
        [t('Responsibility · permissions, boundaries, compliance, audit', '责任 · 权限、边界、合规、能不能审计'), t('overreach, leaks, red lines, traceability, and following the agreed way of working. SwarmAI: privacy, irreversible actions, isolation, the way you set it to work.', '越权、泄露、红线、可追溯，以及有没有照定好的流程做事。SwarmAI：隐私、不可逆操作、隔离、你定的工作方式。')],
        [t('Cost · still worth it at scale', '成本 · 规模化之后还划算吗'), t('tokens, evaluator cost, redundant steps. SwarmAI: cost per turn, read from usage records.', 'token 消耗、评估器成本、步骤冗余。SwarmAI：每轮成本，靠用量记录读数。')],
        [t('Performance · fast and steady', '性能 · 快不快、稳不稳'), t('latency, throughput, stability. SwarmAI: time per turn, read from message times.', '延迟、吞吐、稳定性。SwarmAI：每轮耗时，靠消息时间读数。')],
        [t('Not all five at once', '不用五维都做'), t('start with the two or three that matter most, usually quality and responsibility; cost and performance start in the watch tier.', '起步抓最要命的两三维，多数场景是质量加责任；成本和性能先挂在观测档，只看不拦。')],
      ]} />
    </> },
    { id: 'g-crit', title: t('Writing a criterion: the agent is the subject, the verb observable', '判据怎么写：主语是 agent，谓语可观测'), body: <>
      <P>{t('"Must not leak customer data" is not a criterion; "the output contains no personal data of any customer; any occurrence fails" is. "Answers professionally" is not either: nobody can observe "professional". If you cannot write an observable verb, the criterion is not thought through yet.',
        '"不能泄露客户信息"不是判据；"输出不得包含任何客户个人信息，出现即判失败"才是。"回答要专业"也不是，因为没人能观测"专业"。写不出可观测的谓语，说明这一条还没想清楚。')}</P>
      <P>{t('Once a criterion is set, what the build must record follows from it, in one direction:', '判据定下来之后，构建期要做什么就跟着定了。这是一条单向推导：')}</P>
      <div className="flex items-center gap-2 mt-2 text-small text-[var(--color-text)] flex-wrap">
        {[t('criterion', '判据'), t('which evaluator', '用哪类评估器'), t('the fields it needs', '评估器要哪些字段'), t('what the build records', '构建期必须记录什么')].map((x, i, a) => (
          <span key={x} className="flex items-center gap-2"><span className="px-2 py-0.5 rounded border border-[var(--color-border)]">{x}</span>{i < a.length - 1 && '→'}</span>
        ))}
      </div>
      <P>{t('Not the other way round: building the agent first and adding criteria after is writing the exam for an existing answer. A field not recorded is an evaluation that cannot run.', '反过来不成立：先建好 agent 再补判据，是给已有实现写考试题。字段没记，评估就跑不起来。')}</P>
      <Here>{t('SwarmAI learned this the hard way: the online judge once paired the wrong question and answer because sessions recorded no clear turn boundary; cost per turn had to be derived from a running total; offline runs kept no trajectory, so there was nothing to drill into. The run records now keep the trajectory, steps and answer.',
        'SwarmAI 自己就踩过这一条：线上 judge 一度配错问答，是因为会话记录里没有明确的"轮"边界；每轮成本只能从累计值倒推；离线考题没保存工具轨迹，所以没法下钻。现在运行记录保存了轨迹、步骤和回答。')}</Here>
    </> },
    { id: 'g-matrix', title: t('Two pillars: how closely you look × how hard the evidence is', '两个支柱：看多细 × 证据有多硬'), body: <>
      <P>{t('How closely: only the final answer (black box), the whole process (glass box), one step (white box). How hard: L1 is checked by code, the hardest; L2 is a model’s judgement, which counts only with a fixed scorer; L3 is subjective and by default not scored. Together they make a three-by-three table; picking metrics is picking cells. The cells SwarmAI fills:',
        '看多细：只看最终回答（黑盒）、看整个过程（玻璃盒）、看单个步骤（白盒）。证据有多硬：L1 纯代码判，证据最硬；L2 模型判，只有固定评分器时才算数；L3 主观，默认拒绝打分。两个支柱合起来是一张三乘三的表，选指标就是为你的场景挑格子。SwarmAI 现在填的格子：')}</P>
      <table className="w-full text-small mt-3 border-collapse">
        <thead><tr className="text-[var(--color-text-muted)]"><th />{['L1', 'L2', 'L3'].map((x) => <th key={x} className="text-left font-normal py-1">{x}</th>)}</tr></thead>
        <tbody className="align-top">
          <tr className="border-t border-[var(--color-border)]"><th className="text-left font-normal py-2 pr-3 text-[var(--color-text-muted)]">{t('Black box · final answer', '黑盒 · 最终回答')}</th>
            <td className="py-2 pr-3">{t('online: answers in your language; sessions not corrected', '线上：用提问的语言回答；没被纠正的会话')}</td>
            <td className="py-2 pr-3">{t('offline answers judged; online: delivered or not', '离线考题的回答判定；线上：交付了没有')}</td>
            <td className="py-2 text-[var(--color-text-dim)]">{t('tone · not scored', '语气风格 · 拒绝打分')}</td></tr>
          <tr className="border-t border-[var(--color-border)]"><th className="text-left font-normal py-2 pr-3 text-[var(--color-text-muted)]">{t('Glass box · the process', '玻璃盒 · 整个过程')}</th>
            <td className="py-2 pr-3">{t('offline tool trajectory; online: no text before tool calls', '离线考题的工具轨迹；线上：调工具前不写字')}</td>
            <td className="py-2 pr-3">{t('online: tools chosen sensibly', '线上：工具选得对不对')}</td><td className="py-2 text-[var(--color-text-dim)]">—</td></tr>
          <tr className="border-t border-[var(--color-border)]"><th className="text-left font-normal py-2 pr-3 text-[var(--color-text-muted)]">{t('White box · one step', '白盒 · 单个步骤')}</th>
            <td className="py-2 pr-3">{t('quick checks: each probes one behaviour of one subsystem', '快速检查：每条探一个子系统的一个行为')}</td>
            <td className="py-2 text-[var(--color-text-dim)]">—</td><td className="py-2 text-[var(--color-text-dim)]">—</td></tr>
        </tbody>
      </table>
    </> },
    { id: 'g-judge', title: t('Three ways to score, in this order', '三种打分方式，按这个顺序用'), body: <>
      <Two items={[
        [t('Code (L1) first', '代码（L1）先用'), t('format, values, tool order, compliance: anything that needs no understanding of language. Fast, cheap, reproducible. What code can check never goes to a judge.', '格式、数值、工具顺序、合规，任何不需要理解语言的检查。快、便宜、可复现。能写成代码断言的，绝不交给 judge。')],
        [t('LLM judge (L2)', 'LLM judge（L2）'), t('when meaning must be understood. It has measurable biases: swapping the order changes its verdict, it prefers longer answers, fake citations fool it. Fix the scorer, calibrate against human labels, and prefer a judge from a different family than the agent.', '需要理解语义时用。它有可测量的偏差：调换顺序结论会变、偏爱长回答、会被伪造的引用骗。要固定评分器、和人工判定校准；judge 最好和被测 agent 不同家族。')],
        [t('A person (L3)', '人（L3）'), t('the scarcest, for what matters most: labelling the golden set, spot checks before a release, calibrating the judge. Look at the failures, not everything.', '最稀缺，用在刀刃上：标注 golden set、上线前抽查、校准 judge。只看失败的，不看全部。')],
        [t('The judge can be wrong', 'judge 自己也会错'), t('with an outdated standard it scores a correct answer zero. A judge must first earn it: uncalibrated scores may be shown, never used to release.', '判官用了过期的标准，agent 做对了也会被判零分。评判的人先得自己够格：未校准的分可以公开看，但不能当放行依据。')],
      ]} />
      <Here>{t('A useful self-check: the lower the share of release-blocking criteria that need a judge, the more solid the criteria. See ', '一个好用的自检指标：拦上线的判据里，用 judge 的比例越低，判据定义得越扎实。详见 ')}<L to="method" anchor="judges">{t('Judges, below', '下面的 Judges')}</L>.</Here>
    </> },
    { id: 'g-tiers', title: t('Three tiers of criteria, four checks to release', '判据分三档，放行要过四道关'), body: <>
      <div className="grid grid-cols-3 gap-6 mt-3 text-small text-[var(--color-text)]">
        <div><TierTag tier="red" /><div className="mt-1">{t('fails the release on its own, outside any weighting. No other result makes up for it.', '碰到即否决，不参与加权。分数再高也不放行。')}</div></div>
        <div><TierTag tier="gate" /><div className="mt-1">{t('must pass to release; may carry a threshold, such as a share of traceable citations.', '要过线才放行，可以设一个比例，比如引用可溯源率。')}</div></div>
        <div><TierTag tier="watch" /><div className="mt-1">{t('shown, never blocks. New criteria and judged criteria start here and move up once proven accurate.', '先只看不拦。新判据、judge 判的判据都从这里开始，自己被验证准了再升档。')}</div></div>
      </div>
      <P>{t('A release goes through four checks in a fixed order:', '放行时四道关依序判定，顺序不能换：')}</P>
      <ol className="list-decimal pl-5 text-body text-[var(--color-text)] mt-1 space-y-1">
        <li><b>{t('Red line', '红线')}</b>{t(': one failure blocks.', '：一票否决。')}</li>
        <li><b>{t('Per dimension', '分维过线')}</b>{t(': each dimension on its own; no weighted total, so one dimension’s strength cannot hide another’s weakness.', '：每一维单独看，不做总分加权，一个维度的好不能掩盖另一个的坏。')}</li>
        <li><b>{t('Denominator', '分母核对')}</b>{t(': cases that were skipped, timed out or blocked are not quietly left out. A low score gets investigated, a perfect one does not, so a perfect one needs its denominator checked most.', '：被拦掉、超时、跳过的题不能悄悄排除在外。分数低会有人去查，满分不会，所以满分更要核对分母。')}</li>
        <li><b>{t('Record', '口径留痕')}</b>{t(': write down why this version is released and which known issues it carries.', '：写下这一版为什么放行，带着哪些已知问题。')}</li>
      </ol>
      <Here>{t('In SwarmAI only code-checked quick checks can be gates; a new case starts as watch; every online measure is watch. Each run’s four checks are at the top of ', 'SwarmAI 里只有代码判的快速检查才能当门禁；新 case 从观测开始；线上项全部是观测。每次运行的四道关在 ')}<L to="overview">{t('Overview', '概览')}</L>{t(' and in ', ' 顶部和 ')}<L to="cases" anchor="history">{t('Cases · Run history', '考题 · 运行记录')}</L>{t('.', ' 里。')}</Here>
    </> },
    { id: 'g-onoff', title: t('Offline and online: one prevents, one finds', '离线 vs 线上：一个防，一个查'), body: <>
      <table className="w-full text-small mt-3">
        <thead><tr className="text-left text-[var(--color-text-muted)]"><th /><th className="font-normal py-1">{t('Offline (before release)', '离线（发布前）')}</th><th className="font-normal">{t('Online (after release)', '线上（发布后）')}</th></tr></thead>
        <tbody className="align-top">
          {[
            [t('Answers', '回答什么'), t('did this change break something', '这次改动弄坏了什么吗'), t('is it good in real use now', '真实使用中，现在好不好用')],
            [t('Expected answer', '标准答案'), t('yes, fixed input', '有，输入固定'), t('no; a judge compares the answer with the question only', '没有，judge 只能拿回答和问题本身比')],
            [t('What it fixes', '修的是什么'), t('the agent, to reach the standard', '修 agent，让它够到标准'), t('the standard, to keep up with the real world', '修标准，让标准跟上真实世界')],
            [t('Weakness', '弱点'), t('tests only what is in the set; all passing means no regression, not good', '只测集里有的；全过只代表没退化，不代表好'), t('found after the damage; each item calls a model, so it is sampled', '发现时坏结果已经发生；每条都要调模型，只能抽样')],
          ].map(([a, b, c]) => <tr key={a} className="border-t border-[var(--color-border)]"><td className="py-2 pr-3 text-[var(--color-text-muted)]">{a}</td><td className="py-2 pr-3">{b}</td><td className="py-2">{c}</td></tr>)}
        </tbody>
      </table>
      <Here>{t('Offline = ', '离线 = ')}<L to="cases">{t('Cases', '考题')}</L>{t(' (quick checks + offline cases); online = ', '（快速检查 + 离线考题）；线上 = ')}<L to="online">{t('Live sessions', '真实会话')}</L>.</Here>
    </> },
    { id: 'g-golden', title: t('What makes a good golden case', '什么是好的 golden case'), body: <>
      <ul className="list-disc pl-5 text-body text-[var(--color-text)] mt-2 space-y-1">
        <li><b>{t('Start small: ', '从小集合起步：')}</b>{t('a few dozen, not hundreds. Early on every problem is obvious and a small set catches most regressions.', '几十条，不是几百条。早期每个问题都很明显，小集合就能抓到大部分退化。')}</li>
        <li><b>{t('Mine real failures, ', '从真实的失败里挖，')}</b>{t('do not invent questions. Look at the data first, then set the rubric.', '不是凭空想题。先看数据，再定评分标准。')}</li>
        <li><b>{t('One way of failing per case, ', '一种出错方式一条，')}</b>{t('covering the common, the edge, the compliance and the hand-to-a-person cases.', '覆盖常见、边界、合规、该升级给人的情况。')}</li>
        <li><b>{t('Two people who know the work reach the same verdict; ', '两个懂行的人会给出同样的结论，')}</b>{t('otherwise it is noise.', '这条才算数；否则只是噪声。')}</li>
        <li><b>{t('Judge the outcome, not the exact path; ', '判结果，不死抠路径；')}</b>{t('a check needs teeth: break the thing on purpose and it must fail.', '检查要有牙齿：故意弄坏，它必须失败。')}</li>
      </ul>
      <P>{t('Anti-patterns: rubric before data; trusting an uncalibrated judge; a frozen set that never grows; synthetic data covering up real use.', '反模式：先写评分标准再看数据；judge 没校准就上；评测集冻住不长；用合成数据掩盖真实情况。')}</P>
      <Here>{t('Adding a case runs the checks that code can do (fields, duplicates, empty checks, a negative that must fail, a two-sided rubric, references that exist) before it is saved. See ', '新增 case 时，能用代码检查的几条（字段、重复、空检查、反例必须失败、两面的判定标准、引用存在）在保存前自动检查，见 ')}<L to="cases">{t('Cases', '考题')}</L>.</Here>
    </> },
    { id: 'g-fix', title: t('When a bar is missed', '不过线怎么修'), body: <>
      <ol className="list-decimal pl-5 text-body text-[var(--color-text)] mt-2 space-y-1">
        <li><b>{t('Check the denominator first. ', '先查分母。')}</b>{t('When a score falls short, suspect the score: did something not run, get blocked, time out?', '分数不达标时，第一个该怀疑的是分数本身：是不是有题没跑、被拦掉、超时了。')}</li>
        <li><b>{t('Drill down three levels. ', '三层下钻。')}</b>{t('session: the overall verdict → trajectory: which step → step: what it did. Name both the place and the kind of fault.', '会话级看整体判失败 → 轨迹级锁到哪一步 → 步骤级看那一步做了什么。归因要同时给出位置和故障类别。')}</li>
        <li><b>{t('Change one thing. ', '只改一处。')}</b>{t('Only the step you located, then score again.', '一次只改定位到的那一步，再回去打分。')}</li>
        <li><b>{t('Run the regression set. ', '跑回归。')}</b>{t('Prove you did not fix this and quietly break that.', '证明没有修好这个、悄悄坏了那个。')}</li>
      </ol>
      <Here>{t('SwarmAI’s own example: new offline cases failed on their first run; the cause was the case and the sample workspace, not the agent. Suspect the score and the case before the agent.', 'SwarmAI 自己的例子：新加的离线考题第一次运行就失败，查下来是题目和样例工作区本身有问题，修好题目后通过了。先怀疑分数和题目，再怀疑 agent。')}</Here>
    </> },
    { id: 'g-gaps', title: t('Against the method, what SwarmAI still lacks', '对照方法论，SwarmAI 还差什么'), body: <>
      <ul className="list-disc pl-5 text-body text-[var(--color-text)] mt-2 space-y-1">
        <li><b>{t('Judges not calibrated: ', 'judge 没校准：')}</b>{t('human labels are only starting to be collected, so judged items stay in the watch tier.', '人工判定刚开始收集，judge 判的项目只能在观测档。')}</li>
        <li><b>{t('Judge and agent from one family: ', 'judge 和 agent 同一家族：')}</b>{t('both are Claude models and may favour each other.', '都是 Claude，可能互相偏袒。')}</li>
        <li><b>{t('No bias mitigation: ', '没有偏差缓解：')}</b>{t('a single judge, no swapped-order scoring, no panel of models.', '单个 judge，没有双向打分或多模型评审团。')}</li>
        <li><b>{t('Regression set only: ', '只有回归集：')}</b>{t('no capability set, and no consistency check such as pass^k over repeated runs.', '没有能力评测集，也没有 pass^k 这类多次运行的一致性检查。')}</li>
      </ul>
    </> },
    { id: 'g-src', title: t('Sources', '出处'), body: <>
      <p className="text-small text-[var(--color-text-muted)] mt-2">{t('Agent-DLC, an evaluation-driven agent development lifecycle (internal deck: the six-step flywheel, five dimensions, tiers and checks, choosing evaluators)', 'Agent-DLC 评估驱动的 Agent 开发生命周期（内部讲稿：六环飞轮、五维判据、三档与四道关、选评估器）')}</p>
      {SOURCES.map((s) => (
        <p key={s.url} className="text-small mt-1">{t(s.en, s.zh)}: <a className="text-[var(--color-primary)] hover:underline break-all" href={s.url} target="_blank" rel="noreferrer">{s.url.replace('https://', '')}</a></p>
      ))}
    </> },
  ];

  return (
    <div className="max-w-[860px] mx-auto px-7 pt-10 pb-24" data-testid="eval-guide">
      <div className="text-small text-[var(--color-text-muted)]">Guide</div>
      <div className="text-display font-semibold text-[var(--color-text)] mt-1">{t('Eval-First: how to know whether an agent is good', 'Eval-First：怎么知道一个 agent 好不好')}</div>
      <p className="text-body text-[var(--color-text-muted)] mt-2">
        {t('This page explains Agent-DLC, an evaluation-driven way to build agents, and marks in each section where it lives in SwarmAI. After reading it you should know what every number in this overlay is worth, and how to evaluate your own agent.',
          '这一页讲 Agent-DLC（Agent Development Lifecycle）这套评估驱动的方法，并在每一节标出它在 SwarmAI 里对应的地方。读完你应该能看懂这个 overlay 里每一个数字的分量，也知道自己的 agent 该怎么评。')}
      </p>
      <nav className="flex flex-wrap gap-x-4 gap-y-1 mt-5 text-small">
        {sections.map((s) => <LinkBtn key={s.id} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                                onClick={() => document.getElementById(s.id)?.scrollIntoView({ behavior: 'smooth' })}>{s.title.split(/[:：]/)[0]}</LinkBtn>)}
      </nav>
      {sections.map((s) => (
        <section key={s.id} id={s.id} className="mt-12" data-testid={`guide-${s.id}`}>
          <h2 className="text-title font-semibold text-[var(--color-text)]">{s.title}</h2>
          {s.body}
        </section>
      ))}
    </div>
  );
}

function Wheel() {
  const { t } = useLang();
  const cx = 150, cy = 150, R = 110;
  const go = useGo();
  const names = [t('Define', '定义'), t('Build', '构建'), t('Evaluate', '评估'), t('Release', '发布'), t('Observe', '观测'), t('Improve', '回流')];
  // where each step lives in this overlay (Build is the agent's own work: no tab)
  const target: ([TabId, string?] | null)[] = [['cases'], null, ['cases'], ['cases', 'history'], ['online'], ['overview']];
  return (
    <svg width={300} height={320} viewBox="0 0 300 320" role="img" aria-label="Agent-DLC" className="shrink-0">
      <circle cx={cx} cy={cy} r={R} fill="none" stroke="var(--color-border)" strokeWidth={1.5} />
      <circle cx={cx} cy={cy} r={58} fill="color-mix(in srgb, var(--color-brand) 8%, transparent)" stroke="color-mix(in srgb, var(--color-brand) 40%, transparent)" />
      <text x={cx} y={cy - 4} fill="var(--color-text)" fontSize={13} fontWeight={600} textAnchor="middle">{t('Golden standard', '黄金标准')}</text>
      <text x={cx} y={cy + 14} fill="var(--color-text-muted)" fontSize={10} textAnchor="middle">{t('set · criteria', '测试集 · 五维判据')}</text>
      {names.map((n, i) => {
        const a = -Math.PI / 2 + (i * Math.PI) / 3;
        const x = cx + R * Math.cos(a), y = cy + R * Math.sin(a);
        return (
          <g key={n} data-testid={`wheel-${i}`} className={target[i] ? 'cursor-pointer focus:outline-none [&:focus-visible>circle]:stroke-[var(--color-primary)]' : undefined}
             {...(target[i] ? {
               role: 'link', tabIndex: 0, 'aria-label': n,
               onClick: () => go(target[i]![0], target[i]![1]),
               onKeyDown: (e: ReactKeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(target[i]![0], target[i]![1]); } },
             } : {})}>
            <circle cx={x} cy={y} r={26} fill="var(--color-card)" stroke={i === 3 ? 'var(--color-brand)' : 'var(--color-border)'} />
            <text x={x} y={y + 4} fill="var(--color-text)" fontSize={12} fontWeight={600} textAnchor="middle">{n}</text>
          </g>
        );
      })}
      <text x={cx} y={312} fill="var(--color-text-dim)" fontSize={11} textAnchor="middle">{t('one turn clockwise = one version', '顺时针转一圈 = 升一个版本')}</text>
    </svg>
  );
}
