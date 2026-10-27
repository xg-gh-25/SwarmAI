/**
 * Swarm Out dashboard — STATIC sample data.
 *
 * The dashboard is wired to the real Swarm Out chat tab, but its numbers are not wired
 * to Hive yet: there is no conductor, record layer or cross-run audit aggregate to read
 * from. Everything below is the sample from the design mock
 * (Knowledge/Mockups/2026-10-26-swarm-out/swarm-out-mock.html), shaped like the data a
 * live dashboard will read, so the view can be reviewed now and fed later.
 *
 * Five-dimension verdicts follow the Pipeline v1 run audit (run_protocol.audit):
 * Run Isolation, Quality, Autonomy, Speed, Token Cost — each PASS / WARN / BLOCK, or
 * N/A (does not apply) or PENDING (not reached yet). No composite score.
 */

export type Verdict = 'PASS' | 'WARN' | 'BLOCK' | 'NA' | 'PENDING';

export const DIMENSIONS = ['Run Isolation', 'Quality', 'Autonomy', 'Speed', 'Token Cost'] as const;
export const DIMENSION_ROLE = ['硬约束', '硬约束', '主要目标', '效率', '效率'] as const;

export interface RunJudge {
  /** Outcome line, e.g. "COMPLETED · LANDED · PUSH READY · 7d34ab1". */
  outcome: string;
  verdicts: [Verdict, Verdict, Verdict, Verdict, Verdict];
  numbers: [string, string, string, string, string];
  /** True while the run is still going: values are "so far". */
  live?: boolean;
}

/**
 * The trace of one Swarm Out run, in the order the run is built:
 * PREPARED before it starts (brain → Project Agent → context → Hive placement → start)
 * and DELIVERED after it ends (report → five-dimension audit → end, duration, tokens
 * → what flowed back into the brain). Every field is what the run record will hold;
 * a value not known yet (a run still going) is undefined, never a placeholder.
 */
export interface RunTrace {
  /** The DDD the context came from; undefined = a task with no brain (general agent). */
  brain?: { name: string; version: string; docs: string[] };
  /** From the brain's project-agent.yaml: why this agent ran the task this way. */
  agent?: {
    name: string;
    status: 'reviewed' | 'draft';
    profile: string;
    rule: string;
    validations: string[];
    placement: string;
    landing: string;
  };
  context: {
    goal: string;
    acceptance: string;
    scope?: string;
    slices: { source: string; score: number }[];
    memory: number;
    tokens: string;
    packedAt: string;
  };
  hive: { instance: string; slot: number; session: string; run?: string };
  startedAt?: string;
  endedAt?: string;
  duration: string;
  tokens: string;
  report?: {
    outcome: string;
    deliverables: string[];
    turns: number;
    flowedBack: string[];
    path: string;
  };
}

export interface RunRow {
  id: string;
  title: string;
  sub: string;
  state: 'waiting' | 'running' | 'done' | 'failed' | 'scheduled';
  flag?: string;
  action?: string;
  when?: string;
  stage?: { now: string; next: string };
  eta?: { text: string };
  worker?: string;
  judge?: RunJudge;
  trace?: RunTrace;
}

export interface FleetHive {
  name: string;
  instanceType: string;
  slots: number;
  busy: number;
  status: string;
  healthy: boolean;
}

export const FLEET: FleetHive[] = [
  { name: 'explorer', instanceType: 'm7g.xlarge', slots: 8, busy: 2, status: '忙 2 · 空 6 · 健康', healthy: true },
  { name: 'demo', instanceType: 'm7g.xlarge', slots: 8, busy: 0, status: '暂停分配 · 应用层鉴权不通过', healthy: false },
];

export const WAITING: RunRow[] = [
  {
    id: 'w1', title: '给 Hive 加记录层写权限', state: 'waiting',
    sub: 'pipeline · full · 要改 IAM 策略，属于安全相关 · 来自对话', flag: '要你回答', when: '6 小时',
    judge: {
      outcome: 'AWAITING_HUMAN · BUILD', live: true,
      verdicts: ['PASS', 'PENDING', 'WARN', 'PASS', 'PASS'],
      numbers: ['isolated · 1/1 repo · 0 污染', '还没到验证', '1 个问题等你回答', '11 分 active · 等待不计', '186k · 9/9 次调用有计量'],
    },
  },
  {
    id: 'w2', title: '清理 skill 示例里的本机路径', state: 'waiting',
    sub: 'pipeline · trivial · 完成但没达标：1 个 finding 没关，没自动落地 · 来自 Slack', flag: '落不落地', when: '40 分钟',
    judge: {
      outcome: 'COMPLETED · NOT_LANDED · NOT PUSH READY',
      verdicts: ['PASS', 'WARN', 'PASS', 'PASS', 'WARN'],
      numbers: ['isolated · 1/1 repo · 0 污染', '3/3 验证通过 · 3/4 finding 关闭', '0 决定 · 0 救援', '16 分 · 警戒线 25 分', '212k · 7/8 次调用有计量'],
    },
  },
];

export const RUNNING: RunRow[] = [
  {
    id: 'r1', title: '清掉 skill 文档里的私有引用', sub: 'pipeline · trivial', state: 'running',
    stage: { now: 'BUILD', next: 'REVIEW' }, eta: { text: '还要 10–20 分' }, worker: '#1',
    judge: {
      outcome: 'RUNNING · BUILD', live: true,
      verdicts: ['PASS', 'PENDING', 'PASS', 'PASS', 'PASS'],
      numbers: ['isolated · 1/1 repo · 0 污染', '还没到验证', '0 问题 · 0 救援', '12 分 · 警戒线 25 分', '141k · 6/6 次调用有计量'],
    },
  },
  {
    id: 'r2', title: '调研：Agent SDK 后台会话做法', sub: '调研', state: 'running',
    stage: { now: '读资料', next: '写报告' }, eta: { text: '还要 20–30 分' }, worker: '#2',
    judge: {
      outcome: 'RUNNING · 读资料', live: true,
      verdicts: ['PASS', 'NA', 'PASS', 'PASS', 'PASS'],
      numbers: ['独立工作目录', 'N/A · 调研不产出代码', '0 问题', '18 分 · 警戒线 45 分', '96k'],
    },
  },
];

export const SCHEDULED: RunRow[] = [
  { id: 's1', title: '每日 GitHub Trending', sub: '每天 09:00 · 结果存进 Signals', state: 'scheduled', when: '明天 09:00' },
  { id: 's2', title: 'eval 私有 case 清理检查', sub: '一次 · 只读检查，不改文件 · 来自对话', state: 'scheduled', when: '明天 09:00' },
];

export const ENDED: RunRow[] = [
  {
    id: 'e1', title: '修 steer 回放时间戳错位', state: 'done',
    sub: 'pipeline · bugfix · 已自动落到 main（本机）· PUSH READY · 来自 Slack', when: '22:58',
    judge: {
      outcome: 'COMPLETED · LANDED · PUSH READY · 7d34ab1',
      verdicts: ['PASS', 'PASS', 'PASS', 'PASS', 'WARN'],
      numbers: ['isolated · 1/1 repo · 0 污染', '6/6 验证 · 7/7 finding 关闭', '0 决定 · 0 救援 · 0 审批', '31 分 active · 警戒线 45 分', '519k · 12/13 次调用有计量'],
    },
  },
  {
    id: 'e2', title: '每日 GitHub Trending', sub: '定时 · 结果已存进 Signals', state: 'done', when: '09:02',
    judge: {
      outcome: 'COMPLETED', verdicts: ['PASS', 'NA', 'PASS', 'PASS', 'PASS'],
      numbers: ['独立工作目录', 'N/A · 不产出代码', '0 问题', '4 分', '38k'],
    },
  },
  {
    id: 'e3', title: '调研：后台常驻会话的 3 种做法', sub: '报告已收进 Knowledge/Reports', state: 'done', when: '01:40',
    judge: {
      outcome: 'COMPLETED', verdicts: ['PASS', 'NA', 'PASS', 'WARN', 'PASS'],
      numbers: ['独立工作目录', 'N/A · 不产出代码', '0 问题', '52 分 · 超过警戒线 45 分', '288k'],
    },
  },
  {
    id: 'e4', title: '升级 Hive 上的 Node 依赖', sub: '没完成：npm 源超时 3 次，自己安全结束 · 进展保留', state: 'failed',
    action: '重派', when: '昨天 23:10',
    judge: {
      outcome: 'COMPLETED · NOT_LANDED · checks_exhausted', verdicts: ['PASS', 'NA', 'PASS', 'WARN', 'PASS'],
      numbers: ['isolated · 1/1 repo', 'N/A · checks_exhausted', '0 问题 · 自己到达终态', '27 分 · 超过警戒线 25 分', '102k'],
    },
  },
];

export interface DimensionSummary {
  name: string;
  role: string;
  value: string;
  unit?: string;
  label: string;
  pass: number;
  warn: number;
  block: number;
  na?: number;
  tail: string;
}

export const HISTORY_VERDICT =
  '这周 24 个 run，代码 run 的自主合格交付从 58% 升到 71%（10/14）；隔离 0 污染；1 个 Quality BLOCK 安全地没落地；1 次人工救援。夜里 22–02 点占满过 4 小时，暂时不用加 Hive。';

export const HISTORY_DIMENSIONS: DimensionSummary[] = [
  { name: 'AUTONOMY', role: '主要目标', value: '71', unit: '%', label: '自主合格交付 10/14 · 上周 58%', pass: 17, warn: 6, block: 1, tail: '终态 96%' },
  { name: 'ISOLATION', role: '硬约束', value: '0', label: '跨 run 污染', pass: 24, warn: 0, block: 0, tail: '0 静默共享' },
  { name: 'QUALITY', role: '硬约束', value: '0', label: '落地后被回退', pass: 11, warn: 2, block: 1, na: 10, tail: 'N/A 10' },
  { name: 'SPEED', role: '', value: '31', unit: '分', label: '中位 active · 上周 39', pass: 21, warn: 3, block: 0, tail: '超警戒线' },
  { name: 'TOKEN COST', role: '', value: '288', unit: 'k', label: '中位/run · 上周 351k', pass: 19, warn: 5, block: 0, tail: '计量不全' },
];

/** Runs completed per day, the last 14 days (first 7 = the previous week). */
export const DAILY_COMPLETED = [2, 3, 1, 4, 2, 3, 2, 3, 4, 3, 5, 3, 4, 2];
export const DAILY_LABELS = ['10/13', '10/14', '10/15', '10/16', '10/17', '10/18', '10/19', '10/20', '10/21', '10/22', '10/23', '10/24', '10/25', '10/26'];

/** Concurrent workers per hour (0–8), last 7 days × 24 hours. */
export const OCCUPANCY_DAYS = ['周二', '周三', '周四', '周五', '周六', '周日', '今天'];
export const OCCUPANCY: number[][] = OCCUPANCY_DAYS.map((_, di) =>
  Array.from({ length: 24 }, (_, hr) => {
    const night = hr >= 21 || hr <= 2;
    const day = hr >= 9 && hr <= 18;
    let v = night ? 4 + ((di * 7 + hr) % 4) : day ? 1 + ((di + hr) % 3) : hr % 5 === 0 ? 1 : 0;
    if (night && (di === 1 || di === 4) && (hr === 23 || hr === 0)) v = 8;
    if (di === 6 && hr > 20) v = 0;
    return v;
  }),
);

/** A run's type, as the history groups it: a pipeline profile, or 调研 / 定时. */
export type HistoryType = 'bugfix' | 'trivial' | '调研' | '定时';

export const BY_TYPE: { key: HistoryType; type: string; runs: number; qualified: string; active: string; tokens: string; reason: string }[] = [
  { key: 'bugfix', type: '代码 · pipeline bugfix', runs: 8, qualified: '5 / 8', active: '38 分', tokens: '520k', reason: 'Hive 上缺依赖（2 次）' },
  { key: 'trivial', type: '代码 · pipeline trivial', runs: 6, qualified: '5 / 6', active: '14 分', tokens: '160k', reason: 'finding 没关（1 次）' },
  { key: '调研', type: '调研', runs: 4, qualified: 'N/A', active: '26 分', tokens: '300k', reason: '资料源打不开（1 次）' },
  { key: '定时', type: '定时任务', runs: 6, qualified: 'N/A', active: '4 分', tokens: '40k', reason: '源站超时（1 次）' },
];

/**
 * One ended run in the history: what it was asked to do (the intent) and what came of
 * it, with the brain (DDD) whose context it ran on and the Project Agent that ran it.
 * Either may be absent: a scheduled job or an ad-hoc research task runs with no brain,
 * and a brain's task can run as a plain session with no Project Agent. Code runs
 * (bugfix / trivial) end LANDED or NOT_LANDED; other runs end done or failed.
 */
export interface HistoryRun {
  id: string;
  when: string;
  title: string;
  type: HistoryType;
  brain?: string;
  agent?: string;
  outcome: 'landed' | 'not_landed' | 'done' | 'failed';
  result: string;
  duration: string;
  tokensK: number;
}

const SA = 'SwarmAI';
const SA_AGENT = 'SwarmAI Project Agent';
const AR = 'ai-ready-repo';
const AR_AGENT = 'ai-ready-repo Project Agent';
const SC = 'swarm-content';

/** The runs of the last 7 days, newest first (the 24 the verdict and 按类型 count). */
export const HISTORY_RUNS: HistoryRun[] = [
  { id: 'e2', when: '10/27 09:02', title: '每日 GitHub Trending', type: '定时', outcome: 'done', result: '已存进 Signals', duration: '4 分', tokensK: 38 },
  { id: 'e3', when: '10/27 01:40', title: '调研：后台常驻会话的 3 种做法', type: '调研', brain: SA, outcome: 'done', result: '报告收进 Knowledge/Reports · 1 条判断回流 TECH.md', duration: '52 分', tokensK: 288 },
  { id: 'e4', when: '10/26 23:10', title: '升级 Hive 上的 Node 依赖', type: 'bugfix', brain: SA, agent: SA_AGENT, outcome: 'not_landed', result: '没落地 · npm 源超时 3 次，进展保留', duration: '27 分', tokensK: 102 },
  { id: 'e1', when: '10/26 22:58', title: '修 steer 回放时间戳错位', type: 'bugfix', brain: SA, agent: SA_AGENT, outcome: 'landed', result: '已落到 main 7d34ab1 · 1 条判断回流', duration: '38 分', tokensK: 519 },
  { id: 'h05', when: '10/26 09:01', title: '每日 GitHub Trending', type: '定时', outcome: 'done', result: '已存进 Signals', duration: '3 分', tokensK: 35 },
  { id: 'h06', when: '10/25 21:40', title: '修 Brain Hub 文件树在窄屏下被截断', type: 'bugfix', brain: SA, agent: SA_AGENT, outcome: 'landed', result: '已落到 main', duration: '33 分', tokensK: 460 },
  { id: 'h07', when: '10/25 16:12', title: 'README 补 quick start 截图', type: 'trivial', brain: AR, agent: AR_AGENT, outcome: 'landed', result: '已落到 main', duration: '9 分', tokensK: 120 },
  { id: 'h08', when: '10/25 09:00', title: '每日 GitHub Trending', type: '定时', outcome: 'failed', result: '没完成 · 源站超时', duration: '6 分', tokensK: 21 },
  { id: 'h09', when: '10/24 23:30', title: '修 recall 漏掉归档分片', type: 'bugfix', brain: SA, agent: SA_AGENT, outcome: 'not_landed', result: '没落地 · Quality BLOCK：撤掉修复后新测试仍是绿的', duration: '44 分', tokensK: 610 },
  { id: 'h10', when: '10/24 20:05', title: '统一 skill 文档的触发词写法', type: 'trivial', brain: SA, agent: SA_AGENT, outcome: 'landed', result: '已落到 main', duration: '15 分', tokensK: 170 },
  { id: 'h11', when: '10/24 14:20', title: '调研：桌面端截图对比工具', type: '调研', brain: SA, outcome: 'done', result: '报告收进 Knowledge/Reports', duration: '24 分', tokensK: 260 },
  { id: 'h12', when: '10/24 09:00', title: '每日 GitHub Trending', type: '定时', outcome: 'done', result: '已存进 Signals', duration: '4 分', tokensK: 40 },
  { id: 'h13', when: '10/23 22:47', title: '修检查脚本在 Windows 路径上误报', type: 'bugfix', brain: AR, agent: AR_AGENT, outcome: 'landed', result: '已落到 main', duration: '29 分', tokensK: 380 },
  { id: 'h14', when: '10/23 18:30', title: '整理本周内容选题', type: '调研', brain: SC, outcome: 'done', result: '选题清单 6 条', duration: '28 分', tokensK: 310 },
  { id: 'h15', when: '10/23 11:02', title: '修 pipeline 报告里重复的 finding', type: 'bugfix', brain: SA, agent: SA_AGENT, outcome: 'not_landed', result: '没落地 · Hive 上缺测试依赖，进展保留', duration: '41 分', tokensK: 530 },
  { id: 'h16', when: '10/23 09:00', title: '每日 GitHub Trending', type: '定时', outcome: 'done', result: '已存进 Signals', duration: '4 分', tokensK: 42 },
  { id: 'h17', when: '10/22 23:15', title: 'TECH.md 改掉过时的端口说明', type: 'trivial', brain: SA, agent: SA_AGENT, outcome: 'landed', result: '已落到 main', duration: '11 分', tokensK: 140 },
  { id: 'h18', when: '10/22 20:40', title: '修模板生成的 AGENTS.md 缺小节', type: 'bugfix', brain: AR, agent: AR_AGENT, outcome: 'landed', result: '已落到 main', duration: '36 分', tokensK: 520 },
  { id: 'h19', when: '10/22 15:10', title: '海报文案改成中英双语', type: 'trivial', brain: SC, outcome: 'landed', result: '已落到 main', duration: '13 分', tokensK: 150 },
  { id: 'h20', when: '10/22 10:20', title: 'SKILL.md 示例换成通用路径', type: 'trivial', brain: AR, agent: AR_AGENT, outcome: 'landed', result: '已落到 main', duration: '12 分', tokensK: 160 },
  { id: 'h21', when: '10/21 22:05', title: '修切换 tab 后滚动位置丢失', type: 'bugfix', brain: SA, agent: SA_AGENT, outcome: 'landed', result: '已落到 main', duration: '39 分', tokensK: 540 },
  { id: 'h22', when: '10/21 17:30', title: '修正 CONTRIBUTING 里的命令', type: 'trivial', brain: AR, agent: AR_AGENT, outcome: 'not_landed', result: '没落地 · 1 个 finding 没关', duration: '17 分', tokensK: 180 },
  { id: 'h23', when: '10/21 13:00', title: '调研：开源 Agent 的 eval 做法', type: '调研', outcome: 'failed', result: '没完成 · 资料源打不开', duration: '19 分', tokensK: 150 },
  { id: 'h24', when: '10/21 09:00', title: '每日 GitHub Trending', type: '定时', outcome: 'done', result: '已存进 Signals', duration: '4 分', tokensK: 37 },
];

// ---------------------------------------------------------------------------
// Run traces (sample) — attached to the rows above by id.
// ---------------------------------------------------------------------------

const SWARMAI_BRAIN = { name: 'SwarmAI', version: 'main@b88de602 · 23:15 打包', docs: ['PRODUCT.md', 'TECH.md', 'IMPROVEMENT.md', 'PROJECT.md'] };
const swarmaiAgent = (profile: string, rule: string) => ({
  name: 'SwarmAI Project Agent',
  status: 'draft' as const,
  profile,
  rule,
  validations: ['受影响的 backend pytest', 'frontend vitest + tsc', 'mutation check'],
  placement: 'hive',
  landing: 'host 自动落到本机 main · push 等你',
});

const TRACES: Record<string, RunTrace> = {
  w1: {
    brain: SWARMAI_BRAIN,
    agent: swarmaiAgent('full', '改权限策略或安全相关的配置'),
    context: {
      goal: '让 Hive 能把任务结果写进 S3 的 records/',
      acceptance: '写 Record 的代码有测试；IAM 只加 PutObject 到 records/*',
      scope: 'backend/hive/provisioner.py、IAM 策略',
      slices: [{ source: 'TECH.md § Hive 部署', score: 0.81 }, { source: 'Hive-Mind 设计 § 共享记录层', score: 0.78 }, { source: 'IMPROVEMENT.md § 权限最小化', score: 0.66 }],
      memory: 1, tokens: '7.4k', packedAt: '21:04',
    },
    hive: { instance: 'explorer', slot: 2, session: 'ses_3fa1…c07', run: 'run_7c1e40a2' },
    startedAt: '21:05', duration: '11 分 active · 等你 6 小时', tokens: '186k',
  },
  w2: {
    brain: SWARMAI_BRAIN,
    agent: swarmaiAgent('trivial', '只改文档、不改行为'),
    context: {
      goal: '把 skill 示例里的本机路径换成通用写法',
      acceptance: 'lint_skills 跑绿；示例意思不变',
      slices: [{ source: 'TECH.md § skill 规范', score: 0.74 }, { source: 'AGENT R30#7 · 发布物不带私有标识', score: 0.71 }],
      memory: 1, tokens: '4.2k', packedAt: '22:43',
    },
    hive: { instance: 'explorer', slot: 2, session: 'ses_91d2…e4a', run: 'run_9a27e4b0' },
    startedAt: '22:44', endedAt: '23:00', duration: '16 分', tokens: '212k',
    report: {
      outcome: 'COMPLETED · NOT_LANDED · NOT PUSH READY',
      deliverables: ['改动包：4 个 SKILL.md（没落地，等你决定）'],
      turns: 31,
      flowedBack: [],
      path: 'runs/run_9a27e4b0/REPORT.md',
    },
  },
  r1: {
    brain: SWARMAI_BRAIN,
    agent: swarmaiAgent('trivial', '只改文档、不改行为'),
    context: {
      goal: '清掉 skill 文档里只有本机能解析的路径、run id 和人名',
      acceptance: 'lint_skills 跑绿',
      slices: [{ source: 'AGENT R30#7 · 发布物不带私有标识', score: 0.83 }, { source: 'TECH.md § skill 规范', score: 0.72 }],
      memory: 0, tokens: '3.9k', packedAt: '23:40',
    },
    hive: { instance: 'explorer', slot: 1, session: 'ses_52b8…1d9', run: 'run_4be0c913' },
    startedAt: '23:41', duration: '已跑 12 分', tokens: '141k',
  },
  r2: {
    context: {
      goal: '对比 Claude Agent SDK 的后台会话做法',
      acceptance: '一份带来源的报告，结论在第一段',
      slices: [{ source: 'Library · 2026-08 SDK 笔记', score: 0.69 }],
      memory: 0, tokens: '1.8k', packedAt: '23:34',
    },
    hive: { instance: 'explorer', slot: 2, session: 'ses_c41e…7b0' },
    startedAt: '23:35', duration: '已跑 18 分', tokens: '96k',
  },
  e1: {
    brain: SWARMAI_BRAIN,
    agent: swarmaiAgent('bugfix', '有复现的缺陷'),
    context: {
      goal: 'steer 回放时排到别的消息前面，修好',
      acceptance: '回放顺序和直播一致；新测试撤掉修复会变红',
      scope: 'desktop/src/pages/chat/steerSegments.ts 及其测试',
      slices: [{ source: 'TECH.md § Steer 回放', score: 0.82 }, { source: 'IMPROVEMENT.md § live==reload', score: 0.77 }, { source: 'MEMORY · COE steer 时间戳', score: 0.64 }],
      memory: 2, tokens: '6.1k', packedAt: '22:19',
    },
    hive: { instance: 'explorer', slot: 1, session: 'ses_0e7f…a52', run: 'run_3c81f0d2' },
    startedAt: '22:20', endedAt: '22:58', duration: '38 分', tokens: '519k',
    report: {
      outcome: 'COMPLETED · LANDED · PUSH READY · 7d34ab1',
      deliverables: ['改动 2 个文件，已落到本机 main（7d34ab1）'],
      turns: 64,
      flowedBack: ['1 条判断 → SwarmAI IMPROVEMENT.md：直播和回放的时间必须来自同一个写入方'],
      path: 'runs/run_3c81f0d2/REPORT.md',
    },
  },
  e2: {
    context: {
      goal: '今天的 GitHub Trending，按你的兴趣挑出相关的',
      acceptance: '一份 Signals 条目',
      slices: [{ source: 'USER · 关注方向', score: 0.7 }],
      memory: 1, tokens: '1.1k', packedAt: '08:59',
    },
    hive: { instance: 'explorer', slot: 3, session: 'ses_aa02…f31' },
    startedAt: '09:00', endedAt: '09:04', duration: '4 分', tokens: '38k',
    report: {
      outcome: 'COMPLETED',
      deliverables: ['Signals/2026-10-27-github-trending.md'],
      turns: 7,
      flowedBack: [],
      path: 'records/ses_aa02/report.md',
    },
  },
  e3: {
    brain: SWARMAI_BRAIN,
    context: {
      goal: '后台常驻会话的 3 种做法，给 Swarm Out 选一种',
      acceptance: '结论在第一段，带对比和推荐',
      slices: [{ source: 'Hive-Mind 设计 § Dispatcher', score: 0.85 }, { source: 'TECH.md § session 生命周期', score: 0.73 }],
      memory: 1, tokens: '5.3k', packedAt: '00:47',
    },
    hive: { instance: 'explorer', slot: 1, session: 'ses_7d10…c88' },
    startedAt: '00:48', endedAt: '01:40', duration: '52 分', tokens: '288k',
    report: {
      outcome: 'COMPLETED',
      deliverables: ['Knowledge/Reports/2026-10-27-background-sessions.md'],
      turns: 22,
      flowedBack: ['1 条判断 → SwarmAI TECH.md：调度放在云端队列，Hive 按空槽拉取'],
      path: 'records/ses_7d10/report.md',
    },
  },
  e4: {
    brain: SWARMAI_BRAIN,
    agent: swarmaiAgent('bugfix', '有复现的缺陷'),
    context: {
      goal: '升级 Hive 上的 Node 依赖',
      acceptance: 'npm ci 通过，前端 build 绿',
      slices: [{ source: 'TECH.md § Hive 打包', score: 0.76 }],
      memory: 0, tokens: '3.0k', packedAt: '22:42',
    },
    hive: { instance: 'explorer', slot: 2, session: 'ses_e2c9…04d', run: 'run_b18d27e5' },
    startedAt: '22:43', endedAt: '23:10', duration: '27 分', tokens: '102k',
    report: {
      outcome: 'COMPLETED · NOT_LANDED · checks_exhausted',
      deliverables: ['没完成：进展保留在 Record'],
      turns: 18,
      flowedBack: ['1 条 pitfall → SwarmAI IMPROVEMENT.md：Hive 上 npm 源不稳定，先配镜像'],
      path: 'runs/run_b18d27e5/REPORT.md',
    },
  },
};

for (const row of [...WAITING, ...RUNNING, ...ENDED]) row.trace = TRACES[row.id];
