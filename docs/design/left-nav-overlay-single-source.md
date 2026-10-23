---
title: Left Nav 与 overlay 唯一定义 — 结束不一致和"每次打开都要等"
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: DESIGN v1 — XG 2026-10-23 通过（D1–D5 按倾向）；P0 已落地，P1 起实施
related: ~/.swarm-ai/SwarmWS/Knowledge/Designs/2026-08-02-navcard-overlay-uxui-standard.md（现行标准，部分已过时，见 §7）, 2026-08-04-overlayhost-subsystem-design.md（OverlayHost，已落地）, chat-transcript-single-source.md（同一套方法：先列问题、归根因、先写测试）
---

# Left Nav 与 overlay 唯一定义

## 0. 一句话

左侧导航的每一项（卡片或行）和它打开的 overlay，现在由三处各写一份：nav 里的 JSX
（`ThreeColumnLayout.tsx`）、overlay 注册表（`overlaySurfaces.tsx`）、id 列表的注释
（`overlayIds.ts`）。颜色、分区、名字、打开/关闭的行为在这三处已经对不上。取数一半有缓存、
一半每次重新拉，而 overlay 每次打开都重新挂载，所以有的打开就有内容、有的每次都要等。

修法：**一张导航表定义每一项的一切（nav 和注册表都从它读）；打开/关闭只有一个函数；
overlay 的取数全部走 TanStack Query，定义集中在一处，nav 悬停时预取；几个慢接口单独处理。**
nav 的分组、层级、顺序、样式一律不动（§3）。先写测试（P0），再改。

## 1. 问题清单（2026-10-23 读代码 + 实测，均有出处）

### 1.1 不一致

| # | 问题 | 出处 | 根因 |
|---|---|---|---|
| 1 | 认知区 4 项（C&M、Library、Brain Hub、New Brain）nav 是薄荷绿，overlay 没设 tint，退成 `--color-primary`。现行标准 §1 写的是"overlay 继承卡片所在区的颜色" | `overlaySurfaces.tsx` 这 4 个 `registerOverlay` 没有 `tint` | RC1 |
| 2 | Workspace 在 nav 的 Work 区（青蓝），overlay 无 tint；History、Artifacts 也无 tint | 同上 | RC1 |
| 3 | Capabilities 在 nav 已挪到 System（灰），overlay 仍是 `TINT_WORK` | `overlaySurfaces.tsx` capabilities 条目 | RC1 |
| 4 | 区颜色定义了两份：`A10_GROUP`（3 个）和 `TINT_WORK` / `TINT_SYSTEM`（2 个，没有认知区） | 两个文件 | RC1 |
| 5 | 分区三套说法：nav（Workspace∈Work，Capabilities∈System，History/Artifacts 在 Chat 下）；`overlayIds.ts` 注释（swarmws∈BRAIN，capabilities/history/artifacts∈WORK）；overlay 头部 mode 标签（C&M/Brain Hub/New Brain=BRAIN，Library=LIBRARY，Workspace=WORKSPACE） | `overlayIds.ts`、`overlaySurfaces.tsx` | RC1 |
| 6 | nav 名字和 overlay 标题不一致，标题格式也不一：`C&M`→`C&M · Global Brain`、`Workspace`→`SwarmWS — workspace explorer`、`Brain Hub`→`Brain Hub — phase-1 · read-only lens`、`Library`→`Library · Bookshelf`、`New Brain`→`Grow a new brain`、`Hive`→`Hive · Fleet`、`Capabilities`→`Capabilities — what your AI can do` | 同上 | RC1 |
| 7 | 再点已打开的项：Settings、OS Eval 关闭 overlay；其余 14 项保持打开。Settings、OS Eval 打开前不先关 `activeModal`，其余都先关 | `ThreeColumnLayout.tsx` 每张卡片的 `onClick` | RC3 |
| 8 | 选中态：History、Artifacts 两行打开后无选中样式；Settings 从子入口打开（带 `settingsTab`）时 Settings 卡片不显示选中 | `ThreeColumnLayout.tsx` | RC3 |
| 9 | 尖角对准的元素靠 4 种 testid 命名（`nav-*`、`history-row`、`artifacts-row`、`sidebar-alerts-slot`）。代码注释记过一次改名后尖角失效的事故 | `overlaySurfaces.tsx` `sourceCardTestId` | RC1 |
| 10 | Loading / 空 / 出错状态各写各的：`WorkbenchToolbar` 的一行 "Loading…"、"Loading brains…"、骨架屏、什么都不显示 | 各 `*Overlay.tsx` | RC2 |

### 1.2 加载性能

**后端**（对运行中的 daemon，每个接口 1 次冷 + 4 次热，`$KIROCREW_SCRATCH/nav_ep_timing.py`）：
多数接口 < 20 ms。慢的：

| overlay | 接口 | 冷 | 热（中位数） | 大小 |
|---|---|---|---|---|
| Brain Hub | `/ddd/brains` | 3819 ms | 375–777 ms | 5.6 KB |
| Need You | `/attention` | 752 ms | 85–165 ms | 5.9 KB |
| Artifacts | `/artifacts/products` | 286 ms | 94 ms | 181 KB |
| Library | `/library/native` | 176 ms | 68 ms | 1.0 KB |
| Community | `/community/engagement` | 16 ms | 14 ms | **560 KB** |

**前端**：

| # | 问题 | 出处 | 根因 |
|---|---|---|---|
| 11 | overlay 关闭后内容卸载，下次打开重新挂载 | `OverlayHost.tsx`（`renderedId` 在退场后置 null） | RC2 |
| 12 | 8 个 overlay 用 `useEffect` 自己取数、不缓存：ToDo、Need You、Community、Jobs、Capabilities、Pipeline、Pollinate、Hive。每次打开都先 Loading | 各 `*Overlay.tsx` | RC2 |
| 13 | 5 个用 TanStack Query（C&M、Library、History、Artifacts、Brain Hub），缓存 30 s，过期后仍先显示缓存再刷新。体感上一半快一半慢 | 同上 | RC2 |
| 14 | Brain Hub 打开要等两段：按需加载的代码块 + `/ddd/brains` | `overlaySurfaces.tsx` `lazy(BrainHub)` | RC2、RC4 |
| 15 | 首包 `index-*.js` 3.97 MB（2026-10-23 10:12 的 dist）。17 个 overlay 里 3 个按需加载，其余打进首包。上次拆分时记录的是 3.86 MB | `desktop/dist/assets` | RC4 |

**没测的**：在真机上从点击到内容出现的时间（浏览器工具进不了 Tauri webview）。P0 补一个可测的口径（§5.3）。

## 2. 根因

- **RC1 一项导航没有唯一定义。** 名字、分区、颜色、图标、宽度、尖角目标，nav JSX 写一份，
  注册表写一份，id 注释写一份。改 nav 时（Capabilities 挪区、Community 挪区）只改了一处。
  #1–#6、#9。
- **RC2 取数没有统一规则，且每次打开都重新挂载。** 用不用缓存、缓存多久、Loading 怎么显示，
  由每个 overlay 自己决定。#10–#14。
- **RC3 打开/关闭/切换的行为写在每张卡片的 `onClick` 里。** 16 个 handler 各自决定要不要先关
  modal、再点要不要关。#7、#8。
- **RC4 个别接口和首包本身慢。** #14、#15，以及 `/community/engagement` 的 560 KB。
- **RC5 标准文档过时没人更新。** 现行标准还在讲已删除的 `Modal` fullscreen、`chatAreaBounds`、
  `navSource`、`showOverlay('swarm:show-<x>')`，分区成员也是旧的（Community、Capabilities
  已挪）。新人照标准写，写出的就是旧做法。

## 3. 约束（不动的东西）

- **nav 的分组、层级、顺序、每项的样式全部不动**：Chat hero → History 行 → Artifacts 行 →
  Need You 槽 → 认知区绿面板（C&M、Library、Brain Hub、New Brain）→ Work（ToDo、Workspace、
  Pipeline、Pollinate、Community）→ System（Jobs & Runs、Capabilities、Hive、OS Eval、
  Settings）→ 底栏（GitHub、Terminal）。New Brain 的专用按钮、History/Artifacts 的行样式保留。
  导航表**照现状描述**这套结构，不重新设计它。
- 不改 OverlayHost 的定位方式（`absolute inset-0`）和尖角的缩放修正。
- 不改 `ALL_SHOW_EVENTS` 的安全边界（library、settings、eval、hive 不能被 agent 打开）。
- 各 overlay 的内容和交互不在这次范围，只改取数方式和 Loading 外壳。

## 4. 方案

### S1 一张导航表（解 RC1）

`desktop/src/components/layout/navTable.ts`，取代 `overlayIds.ts` 的列表（`OverlayId`
仍由它导出），每个 id 一行：

```ts
{ id: 'context', zone: 'cognitive', navLabel: 'C&M', title: 'C&M', subtitle: 'Global Brain',
  icon: 'layers', width: 'xl', agentOpenable: true, toggle: true }
```

- `zone`：`chat | cognitive | work | system`，就是 nav 现在的摆放（§3）。overlay 的 tint、
  mode 标签都由 `zone` 推出，不再单独写。
- 区颜色只在这里定义一次（`ZONE_TINT`），`A10_GROUP`、`TINT_WORK`、`TINT_SYSTEM` 删除。
  `chat` 区（History、Artifacts、Need You）用 `--color-primary`（待定 D3）。
- nav 的 JSX **保持现有结构和顺序**，只把字面量换成读表：`label={nav.context.navLabel}`、
  `tint={ZONE_TINT[nav.context.zone]}`。不改成循环渲染，免得动到层级和样式。
- 注册表 `registerOverlay` 只剩 `id` + `render`，其余字段从表里取。
- 尖角目标统一为 `data-nav-id="<id>"`（加在现有元素上，原 testid 保留给测试），
  OverlayHost 按 `activeOverlay` 找它。`sourceCardTestId` 删除。
- `ALL_SHOW_EVENTS` 由表里 `agentOpenable` 推出，安全边界不变（测试锁住那 4 个）。

### S2 打开/关闭只有一个函数（解 RC3）

`OverlayContext` 加 `activateNav(id)`：先关 `activeModal`；已打开同一个 id 时关闭，否则打开。
16 个 `onClick` 都换成它。选中态统一为 `activeOverlay === id`（Settings 带子 tab 打开时也算
选中，待定 D2）。History、Artifacts 行加上和卡片同一套选中样式所需的 `aria-pressed` 和 class
（样式本身沿用行样式，不改成卡片）。

### S3 取数统一（解 RC2）

- `desktop/src/components/layout/overlayQueries.ts`：每个 overlay 打开时要的查询集中定义
  （`queryKey` + `queryFn`），overlay 里用 `useQuery(overlayQueries.todo.list)`。
  8 个 `useEffect` 取数改掉；写操作（ToDo 新建、Jobs run 等）成功后 `invalidateQueries`，
  不再手动重拉。
- 缓存规则一条：`staleTime` 30 s（和现有 5 个一致），过期后**先显示缓存、后台刷新**。
  需要更实时的（Hive 的 provisioning、Pipeline 运行中）用 `refetchInterval`，只在打开时轮询。
- nav 项 `onMouseEnter` / `onFocus` 时 `prefetchQuery` 同一组查询，并预取按需加载的代码块。
- 一个 `OverlayState` 组件统一 Loading（首次无数据时）、空、出错（带重试）三态，替换 #10
  的各种写法。已有数据时刷新不显示 Loading，只在 `WorkbenchToolbar` 显示一个小的刷新标记。
- 不保留卸载后的 React 状态（不做 keep-alive）：有缓存后重新挂载就是立即出内容，keep-alive
  会让 17 个 overlay 常驻内存，还要处理各自的过期状态，得不偿失。

### S4 慢点单独处理（解 RC4）

- `/ddd/brains` 冷启动 3.8 s：先给三条并行支路（attention、git status、git log）和
  `_brain_summary` 加计时日志，量出是哪段，再决定（不先猜）。
- `/community/engagement` 560 KB：先查 overlay 用到哪些字段，再定是分页还是只返回摘要。
- `/attention` 冷 752 ms：同样先计时。
- 首包：其余 overlay 改为 `lazy`（悬停时预取代码块，所以首次打开也不等）；跑一次包体分析
  （`vite build` + `rollup-plugin-visualizer`，只在本地分析，不进依赖）找首包里其他大块。

### S5 更新标准文档（解 RC5）

同一批改动里改 2026-08-02 标准：§1 区颜色的定义处改为 `navTable.ZONE_TINT`；§2 分区成员按
现状；§2 加卡片的规则改为"在导航表加一行 + `activateNav`"；§3 改为 OverlayHost + 注册表
（删除 Modal fullscreen、`chatAreaBounds`、`navSource`、`useExclusiveOverlay` 的说法）；
新增"取数：只用 `overlayQueries` + `OverlayState`"一节。

## 5. 验收与 P0 测试

先写这些测试，跑出的不一致即问题清单的证据，每个阶段只许让失败变少。

1. **契约测试** `navTable.contract.test.tsx`：渲染真实的 `ThreeColumnLayout`，对表里每个 id
   断言：nav 上能找到 `data-nav-id`；显示的名字 = `navLabel`；卡片的 `--ac` = 它所在区的颜色；
   OverlayHost 打开它时 `--panel-accent` = 同一个颜色、标题 = `title`；尖角存在。
   另断言 `ALL_SHOW_EVENTS` 不含 library、settings、eval、hive。
2. **行为测试**：对每个 id，点击 → 打开且选中；再点 → 关闭（D1 定了之后）；有 `activeModal`
   时点击 → modal 先关。
3. **取数测试** `overlayReopen.test.tsx`：每个 overlay 用 mock 服务打开一次、关掉、再打开：
   第二次打开**不出现 Loading**，且 30 s 内不发第二次请求。悬停 nav 项后打开：不出现 Loading。
4. **首包上限**：`npm run build` 后检查 `index-*.js` 不超过一个上限（定在 S4 做完后的实测值
   + 5%），超了测试失败。
5. **真机口径**：OverlayHost 在 dev 构建里打 `performance.mark('overlay-open:<id>')` 和内容
   首次有数据时的 mark，console 打印耗时。XG 在真机上点一遍 17 项，记一次前后对比。生产构建
   不打印。

## 6. 实施顺序

| 阶段 | 内容 | 规模 | 风险 |
|---|---|---|---|
| P0 | §5 的测试 1–3（先红） | 中 | 无 |
| P1 | S1 导航表 + S2 `activateNav`：#1–#9 | 中 | 中：碰 nav 文件，必须不改结构；两份 nav 测试（DOMAIN_ORDER、expectedNavOrder）同一提交更新 |
| P2 | S3 取数统一，一个 overlay 一个提交（8 个），再加悬停预取和 `OverlayState` | 中 | 低：每个独立，契约测试兜底 |
| P3 | S4：先计时再修 `/ddd/brains`、`/attention`、`/community/engagement`；按需加载 + 首包上限 | 中 | 中 |
| P4 | S5 更新标准文档 | 小 | 无 |

P1 先于 P2：契约测试需要导航表才能遍历所有 id。每个阶段跑：两份 nav 测试、
`overlayIds.test.ts`（改名后的契约测试）、`overlayHostE2E.test.tsx`、受影响 overlay 的测试、
`tsc -b`、eslint。

## 7. 不做什么

- 不重新设计 nav 的分组、层级、样式（§3）。
- 不做 overlay keep-alive（§S3 理由）。
- 不改各 overlay 内部的功能和布局。
- 不处理 Settings / Eval 内部 tab 的取数（它们已是 TanStack Query），除非契约测试暴露问题。

## 8. XG 的决定（2026-10-23：五项都按下面的倾向）

- **D1 再点已打开的项**：统一为"关闭"（现在 Settings、OS Eval 的做法），还是统一为"保持打开"
  （其余 14 项的做法）？我倾向关闭：nav 项就像开关，第二次点击没有别的含义。
- **D2 Settings 带子 tab 打开时**，Settings 卡片显示选中吗？我倾向显示：打开的就是 Settings。
- **D3 Chat 区三项（History、Artifacts、Need You）的 overlay 颜色**：用主题色（现在的样子），
  还是给 Chat 区定一个区颜色？我倾向主题色，不新增颜色。
- **D4 overlay 标题**：改成和 nav 名字一致、副标题另起一行（`C&M` / `Global Brain`），还是
  保留现在的长标题？我倾向改：用户点的是 "C&M"，打开看到的也该是 "C&M"。Workspace 的标题
  因此会从 "SwarmWS" 变成 "Workspace"。
- **D5 mode 标签**：由区推出（BRAIN / WORK / SYSTEM / CHAT），还是保留每个 overlay 自己的
  标签（HISTORY、PIPELINE…）？我倾向保留各自的：标签说的是"这是什么"，区已经由颜色表达了。

## 9. P0 结果（2026-10-23）

`desktop/src/components/layout/navOverlayContract.test.tsx`：真实的 `LeftSidebar` + `OverlayHost` +
注册的 overlay，经真实 provider 渲染；只 mock HTTP（`services/api`，每次 GET 记下 URL）和 pty，
所以统计到的请求就是 app 会发的请求。对 16 个 nav 项逐个检查 §5 的 1–3（tint、title、spout、
selected、toggle、modalFirst、refetch、prefetch），再加 Settings 带子 tab 时的选中（D2）和
Need You 的 tint/title。发现的分歧必须与 `KNOWN` 完全相等：多一条失败，修好了没删也失败。

跑出 51 条，按根因：

| 根因 | 条数 | 内容 |
|---|---|---|
| RC1 | 13 | tint：context、library、brain-hub、new-brain、swarmws、capabilities；title：前 5 个 + capabilities、hive |
| RC3 | 19 | selected：history、artifacts、settings 带子 tab；toggle：除 Settings、OS Eval 外的 14 项；modalFirst：Settings、OS Eval |
| RC2 | 19 | refetch：todo、community、jobs、capabilities、pipeline、pollinate、hive；prefetch：有取数的 12 项全部 |

对照：History、Artifacts、C&M、Library、Brain Hub（已用 TanStack Query）没有 refetch，Hive、
Jobs 已有正确的 tint，spout 全部正常——说明各项检查能区分对错，不是一律报错。连跑两次结果相同。

§5.4（首包上限）、§5.5（真机计时）放到 P3，和按需加载一起做。
