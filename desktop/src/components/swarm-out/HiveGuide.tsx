/**
 * HiveGuide — what the Swarm Out panel shows when there is no running Hive to dispatch to.
 *
 * Swarm Out sends tasks to the person's own Hives, so with none running it can do
 * nothing; this card says so and walks through the setup in the Hive surface (left-nav
 * Hive card), naming its buttons as they are labelled there. Two cases:
 *  - no Hive at all: what a Hive is, then the three setup steps;
 *  - Hives, none running: which ones and their status, and to start one.
 * The "打开 Hive" button is the person's own click, so it may open the Hive overlay
 * (the agent cannot: Hive is outside the ui_action allowlist).
 */
import type { SwarmOutHive } from '../../services/swarmOut';

const card = 'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const muted = 'text-[var(--color-text-muted)]';
const btn = 'px-3 py-1.5 rounded-md text-[12.5px] font-medium bg-[var(--color-primary)] text-white hover:opacity-90';

export function HiveGuide({ hives, onOpenHive }: { hives: SwarmOutHive[]; onOpenHive?: () => void }) {
  const open = onOpenHive && (
    <button type="button" className={btn} onClick={onOpenHive} data-testid="swarm-out-open-hive">打开 Hive</button>
  );
  if (hives.length > 0) {
    return (
      <section className={`${card} px-4 py-3 mb-4`} data-testid="swarm-out-hive-guide" data-case="none-running">
        <div className="font-medium">没有在运行的 Hive</div>
        <div className={`mt-1 text-[12.5px] ${muted}`}>
          {hives.map((h) => `${h.name}（${h.status}）`).join('、')}。在 Hive 里启动一台，就能派任务。
        </div>
        {open && <div className="mt-3">{open}</div>}
      </section>
    );
  }
  return (
    <section className={`${card} px-4 py-3 mb-4`} data-testid="swarm-out-hive-guide" data-case="no-hive">
      <div className="font-medium">还没有 Hive，任务还派不出去</div>
      <div className={`mt-1 text-[12.5px] ${muted}`}>
        Hive 是部署在你自己 AWS 账号里的一台 SwarmAI。任务派到 Hive 上跑，合上 Mac 也会继续；需要你拍板的问题会出现在这里。
      </div>
      <ol className="mt-2 mb-0 pl-5 text-[12.5px] leading-6">
        <li>打开 Hive，在 Accounts 里点 Add Account，添加你的 AWS 账号。</li>
        <li>在 Fleet 里点 Deploy Hive，等它显示 running（通常要几分钟）。</li>
        <li>回到这里，在右边的对话里说要做什么，Swarm 跟你确认后派出去。</li>
      </ol>
      {open && <div className="mt-3">{open}</div>}
    </section>
  );
}
