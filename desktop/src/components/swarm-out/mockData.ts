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

export interface RunRow {
  id: string;
  title: string;
  sub: string;
  state: 'waiting' | 'running' | 'done' | 'failed' | 'scheduled';
  flag?: string;
  action?: string;
  when?: string;
  stage?: { now: string; next: string };
  eta?: { progress: number; text: string };
  worker?: string;
  judge?: RunJudge;
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
    stage: { now: 'BUILD', next: 'REVIEW' }, eta: { progress: 55, text: '还要 10–20 分' }, worker: '#1',
    judge: {
      outcome: 'RUNNING · BUILD', live: true,
      verdicts: ['PASS', 'PENDING', 'PASS', 'PASS', 'PASS'],
      numbers: ['isolated · 1/1 repo · 0 污染', '还没到验证', '0 问题 · 0 救援', '12 分 · 警戒线 25 分', '141k · 6/6 次调用有计量'],
    },
  },
  {
    id: 'r2', title: '调研：Agent SDK 后台会话做法', sub: '调研', state: 'running',
    stage: { now: '读资料', next: '写报告' }, eta: { progress: 40, text: '还要 20–30 分' }, worker: '#2',
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

export const BY_TYPE = [
  { type: '代码 · pipeline bugfix', runs: 8, qualified: '5 / 8', active: '38 分', tokens: '520k', reason: 'Hive 上缺测试依赖（2 次）' },
  { type: '代码 · pipeline trivial', runs: 6, qualified: '5 / 6', active: '14 分', tokens: '160k', reason: 'finding 没关（1 次）' },
  { type: '调研', runs: 4, qualified: 'N/A', active: '26 分', tokens: '300k', reason: '资料源打不开（1 次）' },
  { type: '定时任务', runs: 6, qualified: 'N/A', active: '4 分', tokens: '40k', reason: 'npm 源超时（1 次）' },
];
