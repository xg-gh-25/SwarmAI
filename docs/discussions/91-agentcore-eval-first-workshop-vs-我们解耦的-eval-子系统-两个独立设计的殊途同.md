---
title: "AgentCore Eval-First Workshop vs 我们解耦的 Eval 子系统 —— 两个独立设计的殊途同归"
created: 2026-06-29
updated: 2026-06-29
status: published
---
<!-- GitHub Discussion #91: https://github.com/xg-gh-25/SwarmAI/discussions/91 -->
> 🌐 中文 | English → #90 · 相关: #74 OS Eval vs AgentCore · #89 认知进化

# 中文版 — AgentCore 的 Eval-First Workshop vs 我们解耦的 Eval 子系统:两个独立设计在哪里殊途同归,在哪里分道

> AWS 出了一个 hands-on sample —— *Eval-First: Building Enterprise Agents with
> AgentCore* —— 带两个**可以真正读代码**的 custom code-based evaluator:
> **THELMA**(单轮 RAG 质量)和 **Mind the Goal**(多轮目标达成)。我们在做一个
> 自进化 agent OS,有自己的 eval 子系统。我读了他们 evaluator 的*代码*(不是
> README),和我们的并排放。有意思的不是差异 —— 是两个独立团队收敛到了同一个
> 反直觉的信念上。

## 收敛点:没有归因的分数毫无价值

大多数 eval 工具停在一个数字:"groundedness 0.62"。光这个没用 —— 0.62 *是因为
什么?* 检索差?模型在编?source 文档脏?你没法对分数下手,你只能对*原因*下手。

AWS 的两个 evaluator 都围绕这点构建,我们也是:

- **THELMA** 不只吐一个 groundedness 分。它的 `interplay.py` 把 7 个子分的*组合*
  读成一个鉴别诊断:`SP1 高 + SP2 低` → 你的 source chunk 是脏的(看着相关、
  其实多半是噪声);`SQC↓ + RQC↑ + GR↓` → 模型在编,改 prompt;`SQC↓ + RQC↓`
  → 检索失败,改 retriever。分数是症状,interplay 是诊断。
- **Mind the Goal** 不只吐一个 Goal Success Rate。每个失败的 goal 都从固定分类法
  里拿一个 **RCOF 码**(Root Cause of Failure)—— E1 语言理解 / E2 拒答 / E3 错误
  检索 / E4 检索失败 / E5 系统错误 / E6 错误路由。GSR 告诉你*坏了多少*,RCOF
  分布告诉你*先修哪个*。

这和我们 pipeline 的 REPRO gate 在内部强制的信念一模一样:**先诊断,后动作。**
我们在 publish 处阻塞一个修复,除非它带着观察证据、而不是推断出的原因 —— 因为
快速收敛到错误诊断,比慢速收敛到正确诊断更糟。看到 AWS 的 *evaluator* 从一个
完全不同的起点(企业 RAG QA,不是编码 agent)编码出同样的"分数→原因→动作"
形状,是我们见过的最强信号:这个形状是对的。

## 他们的设计实实在在教了我们三件事

我本以为是去验证我们的做法,结果带回三个可借鉴的点。

**1. capability 与 regression 分离。** 他们的 workshop 把 *capability* eval
(起始分低、"一座要爬的山"、驱动改进)和 *regression* eval(接近 100%、维持
baseline、上 CI)分开。一个成熟的 capability case 会**毕业**进 regression 套件。
我们所有 case 在一个池子里 —— 这正是为什么一次部分失败的 run 读成一个含糊的
"0.0",而不是"capability 前沿在 60%、regression baseline 守在 100%"。两个不同的
问题,两个不同的门。

**2. RCOF 作为运行时失败分类法。** 我们的 correction 登记表有一个*认知*面的分类
(CLASS A "我写的所以能用"、CLASS B "没验证就推断"、CLASS C "改错层")。我们缺
的是一个 eval 失败的 *agent 运行时* 分类 —— 正是 Mind the Goal 的 E1–E6。两者
互补:他们归因一次*运行*失败,我们归因一次*判断*失败。

**3. 偏差缓解作为 judge 的一等属性。** workshop 明确讲 LLM-judge 偏差 —— 位置
偏差(换 A/B 顺序,结论翻转)、冗长偏差、权威偏差 —— 和缓解手段(双向打分、
judge 评审团)。我们 pin 住 judge(固定模型 + T=0),但还没做双向打分。加起来
很便宜,而且直接加固了 L2(模型打分)证据层。

## 我们在哪里分道 —— 以及为什么

这不是"他们更先进"。我们的子系统做了三个他们(按设计)没做的架构下注,因为
我们是另一种系统:

| 维度 | AgentCore sample | 我们的子系统 |
|------|------------------|-------------|
| **eval 在哪跑** | Lambda,由 AgentCore eval 服务在某条 trace 上调用 | 解耦的顶层子系统(`Eval/`),由 CI / 部署 / 定时触发 —— 绝不在它评判的工作内部跑 |
| **eval IP 归属** | 你的 evaluator 代码是你的;平台负责调度 | 同样的信念,更进一步:golden set 拆成**公开**(git 跟踪、引用公开代码)+ **私有**(gitignore、引用内部状态),加载时按 `_origin` tag 合并,跨文件碰撞 fail loud |
| **它评什么** | agent 的任务输出(RAG 答案、目标达成) | agent 的输出**以及它自己的认知** —— 一个"behavior tier",评 OS 是否遵守了自己的宪法 |
| **触发纪律** | on-demand / online / batch | **git-bound、fail-closed gate** —— eval 是系统级关注,部署后触发,结构上禁止在 coding pipeline 内跑(否则测的是没部署的 binary) |

AWS 说的那条最深的共同原则 —— *"评估平台可以买,评估内容必须自控"* —— 是我们
早已立为第一性原理的东西(我们叫 eval-as-IP)。他们的 *托管平台 + 你的 evaluator*
拆分,是同一个想法的产品化版本,而我们解耦的 `Eval/` 子系统是它在单人 OS 里的
表达。

## 一句话带走

如果你在做 agent eval:**让 evaluator 吐一个原因,而不只是一个分数。** THELMA 的
score-interplay 诊断和 Mind the Goal 的 RCOF 分类法,是这件事两个干净、可读的
参考实现 —— 而且是 MIT-0。数字告诉你哪里错了;只有归因告诉你周一早上该做什么。

参考:AWS sample(MIT-0)—
https://github.com/aws-samples/sample-eval-first-building-enterprise-agents-with-agentcore
· THELMA 论文 arXiv:2505.11626 · Mind the Goal 论文 arXiv:2510.03696
