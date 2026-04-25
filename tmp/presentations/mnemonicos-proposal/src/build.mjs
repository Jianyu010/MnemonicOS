import {
  Presentation,
  PresentationFile,
  row,
  column,
  grid,
  layers,
  panel,
  text,
  shape,
  rule,
  fill,
  hug,
  fixed,
  grow,
  fr,
} from "@oai/artifact-tool";
import { readdir, unlink, writeFile } from "node:fs/promises";

const W = 1920;
const H = 1080;

const C = {
  bg: "#F6F8FB",
  ink: "#101828",
  muted: "#667085",
  line: "#D5DAE3",
  dark: "#0D1321",
  blue: "#2F6BFF",
  cyan: "#2CB8C7",
  green: "#12A66A",
  amber: "#C97A00",
  red: "#E85D5D",
  violet: "#7357FF",
  softBlue: "#EAF1FF",
  softCyan: "#E7F8FA",
  softGreen: "#E9F7F1",
  softAmber: "#FFF2D7",
  softRed: "#FDECEC",
  softViolet: "#EFEAFE",
  paper: "#FFFFFF",
};

const F = {
  title: { fontSize: 48, bold: true, color: C.ink },
  sub: { fontSize: 24, color: C.muted },
  body: { fontSize: 22, color: C.ink },
  small: { fontSize: 16, color: C.muted },
  micro: { fontSize: 12, color: "#8A94A6" },
};

function tx(value, opt = {}) {
  return text(value, {
    name: opt.name,
    width: opt.width ?? fill,
    height: opt.height ?? hug,
    columnSpan: opt.columnSpan,
    rowSpan: opt.rowSpan,
    style: { ...F.body, ...(opt.style || {}) },
  });
}

function bg(color = C.bg) {
  return shape({ name: "bg", width: fill, height: fill, fill: color, line: { width: 0, fill: color } });
}

function label(value, color = C.blue) {
  return row({ width: fill, height: fixed(34), gap: 10, align: "center" }, [
    shape({ width: fixed(10), height: fixed(10), fill: color, line: { width: 0, fill: color }, borderRadius: "rounded-full" }),
    tx(value, { width: grow(1), style: { fontSize: 16, bold: true, color } }),
  ]);
}

function footer(source = "MnemonicOS proposal | revised narrative") {
  return row({ width: fill, height: fixed(22), justify: "between", align: "center" }, [
    tx(source, { width: fixed(900), style: F.micro }),
    tx("2026-04-24", { width: fixed(260), style: { ...F.micro, alignment: "right" } }),
  ]);
}

function slide(p, kicker, title, subtitle, body, accent = C.blue) {
  const s = p.slides.add();
  s.compose(
    layers({ width: fill, height: fill }, [
      bg(),
      column({ width: fill, height: fill, padding: { x: 84, y: 56 }, gap: 22 }, [
        column({ width: fill, height: hug, gap: 10 }, [
          label(kicker, accent),
          tx(title, { name: "slide-title", style: F.title }),
          subtitle ? tx(subtitle, { name: "slide-subtitle", style: F.sub, width: fill }) : null,
        ].filter(Boolean)),
        body,
        footer(),
      ]),
    ]),
    { frame: { left: 0, top: 0, width: W, height: H }, baseUnit: 8 },
  );
}

function card(title, body, accent = C.blue, fillColor = C.paper, opt = {}) {
  return panel(
    {
      fill: fillColor,
      line: { width: 1, fill: opt.line || accent },
      borderRadius: "rounded-md",
      padding: opt.padding || { x: 20, y: 18 },
      width: opt.width ?? fill,
      height: opt.height ?? fill,
    },
    column({ width: fill, height: fill, gap: 9 }, [
      tx(title, { style: { fontSize: opt.titleSize || 25, bold: true, color: accent } }),
      tx(body, { style: { fontSize: opt.bodySize || 18, color: opt.bodyColor || C.ink } }),
    ]),
  );
}

function chip(value, fillColor, color = C.ink, width = 190) {
  return panel(
    {
      fill: fillColor,
      line: { width: 1, fill: fillColor },
      borderRadius: "rounded-md",
      padding: { x: 12, y: 8 },
      width: fixed(width),
      height: fixed(44),
      align: "center",
      justify: "center",
    },
    tx(value, { width: fill, style: { fontSize: 17, bold: true, color, alignment: "center" } }),
  );
}

function arrow() {
  return tx("→", { width: fixed(30), style: { fontSize: 30, bold: true, color: "#8A94A6", alignment: "center" } });
}

function bullets(items, size = 21, color = C.ink) {
  return column({ width: fill, height: hug, gap: 9 }, items.map((x) => tx(`• ${x}`, { style: { fontSize: size, color } })));
}

function stepPill(num, name, body, accent, fillColor) {
  return panel(
    { fill: fillColor, line: { width: 1.2, fill: accent }, borderRadius: "rounded-md", padding: { x: 22, y: 18 }, width: fill, height: fill },
    column({ width: fill, height: fill, gap: 12 }, [
      row({ width: fill, height: hug, gap: 14, align: "center" }, [
        tx(num, { width: fixed(72), style: { fontSize: 44, bold: true, color: accent } }),
        tx(name, { style: { fontSize: 27, bold: true, color: C.ink } }),
      ]),
      tx(body, { style: { fontSize: 20, color: C.ink } }),
    ]),
  );
}

function buildDeck() {
  const p = Presentation.create({ slideSize: { width: W, height: H } });

  // Cover
  {
    const s = p.slides.add();
    s.compose(
      layers({ width: fill, height: fill }, [
        bg("#F4F7FB"),
        shape({ width: fixed(720), height: fill, fill: C.dark, line: { width: 0, fill: C.dark } }),
        row({ name: "cover-main-row", width: fill, height: fill, padding: { x: 92, y: 70 }, gap: 78, align: "center" }, [
          column({ width: fixed(650), height: fill, justify: "center", gap: 28 }, [
            label("STEP 0 + STEP 1", C.cyan),
            tx("MnemonicOS", { style: { fontSize: 86, bold: true, color: "#FFFFFF" } }),
            tx("True-Raw MemoryOS\n+ Learned Daemon", { style: { fontSize: 42, bold: true, color: "#BFEAF0" } }),
            rule({ width: fixed(260), stroke: C.cyan, weight: 5 }),
            tx("把未经解释的经验保存为可治理、可追溯、可学习的记忆操作系统；再用小模型 daemon 与 RL / preference loop 优化生命周期与 serving。", {
              width: fixed(590),
              style: { fontSize: 25, color: "#E9EEF7" },
            }),
          ]),
          column({ width: grow(1), height: fill, justify: "center", gap: 28 }, [
            row({ name: "cover-flow-top", width: fill, height: hug, gap: 10, align: "center" }, [
              chip("Raw", C.softBlue, C.blue, 130), arrow(), chip("Projection", C.softCyan, C.cyan, 180), arrow(), chip("Candidate", C.softAmber, C.amber, 180), arrow(), chip("Canonical", C.softGreen, C.green, 180),
            ]),
            row({ width: fill, height: hug, gap: 10, align: "center", justify: "center" }, [
              tx("↓", { width: fixed(40), style: { fontSize: 38, bold: true, color: C.muted, alignment: "center" } }),
            ]),
            row({ name: "cover-flow-bottom", width: fill, height: hug, gap: 10, align: "center" }, [
              chip("Policy Packet", "#EEF0F5", C.ink, 210), arrow(), chip("Outcome Trace", "#EEF0F5", C.ink, 210), arrow(), chip("Learned Daemon", C.softViolet, C.violet, 250),
            ]),
            panel({ fill: C.paper, line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: { x: 28, y: 24 }, width: fill, height: hug },
              tx("核心：raw 不是要直接塞给 LLM；raw 是 MemoryOS 可重新投影、验证、训练和回滚的操作基底。Serving 必须离 raw 足够远，只返回被治理的最小有效上下文。", {
                style: { fontSize: 28, bold: true, color: C.ink },
              }),
            ),
          ]),
        ]),
      ]),
      { frame: { left: 0, top: 0, width: W, height: H }, baseUnit: 8 },
    );
  }

  // 1 Motivation
  slide(
    p,
    "BACKGROUND + MOTIVATION",
    "当前 agent memory 的问题不是“检索不够好”，而是边界过早停在 clean context",
    "Memory 确实提升 agent 可用性；问题在于：如果 memory layer 只保存被总结、截断、格式化后的片段，它就失去了后续治理、再解释和训练的操作空间。",
    grid({ width: fill, height: grow(1), columns: [fr(0.95), fr(1.05)], columnGap: 42 }, [
      column({ width: fill, height: fill, gap: 18 }, [
        tx("为什么不是“恰到好处”？", { style: { fontSize: 29, bold: true, color: C.ink } }),
        card("已知事实：memory 有效", "跨 session 记忆、偏好保持、项目上下文复用，会显著提升 agent 连续性与可用性。", C.green, C.softGreen, { height: fixed(120) }),
        card("但它会引入新失败模式", "过期结论、错误归因、上下文污染、细节过载、不可追溯更新，会让 agent 更自信地错。", C.red, C.softRed, { height: fixed(120) }),
        card("MemoraX AI 研究启发", "过细、过贴近 raw 的 skill / memory 可能拖累 LLM 处理效果；真正需要的是选择、抽象、分层和谨慎 serving。", C.violet, C.softViolet, { height: fixed(132), bodySize: 18 }),
        tx("推论：raw 的价值不在“更多上下文”，而在“可重新投影、可审计、可训练、可回滚”。", {
          style: { fontSize: 27, bold: true, color: C.ink },
        }),
      ]),
      column({ width: fill, height: fill, gap: 18 }, [
        tx("一个 MemoryOS 应该把两件事分开", { style: { fontSize: 29, bold: true, color: C.ink } }),
        panel({ fill: C.dark, line: { width: 0, fill: C.dark }, borderRadius: "rounded-md", padding: { x: 28, y: 24 }, width: fill, height: fixed(255) },
          column({ width: fill, height: fill, gap: 16 }, [
            tx("Raw side：尽量完整", { style: { fontSize: 32, bold: true, color: "#FFFFFF" } }),
            bullets(["保留原始证据、时间、来源、上下文与操作痕迹", "允许未来更好的 extractor / daemon 重新解释", "作为 provenance、training trace、rollback 的共同根"], 21, "#E9EEF7"),
          ]),
        ),
        panel({ fill: C.paper, line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: { x: 28, y: 24 }, width: fill, height: fixed(255) },
          column({ width: fill, height: fill, gap: 16 }, [
            tx("Serving side：尽量克制", { style: { fontSize: 32, bold: true, color: C.ink } }),
            bullets(["不直接暴露 raw；返回 policy-aware packet", "用 freshness、confidence、warning、verification action 控制风险", "让 LLM 看到最小必要信息，而不是细节洪水"], 21),
          ]),
        ),
        card("因此需要 MemoryOS，而非简单 RAG / memory system", "RAG 解决“找什么”；MemoryOS 还要解决“什么能进来、如何变成记忆、何时过期、何时警告、什么反馈会改变系统”。", C.blue, C.softBlue, { height: grow(1), titleSize: 27, bodySize: 20 }),
      ]),
    ]),
    C.blue,
  );

  // 2 Architecture
  slide(
    p,
    "MNEMONICOS VISION",
    "MnemonicOS 是一个 agent-independent 的记忆运行时：四个 flange 包住核心知识机器",
    "它不替代 agent、数据库、向量引擎或文档转换器；它拥有 memory contract、lifecycle 和 policy semantics。",
    column({ width: fill, height: grow(1), gap: 22 }, [
      grid({ width: fill, height: fixed(470), columns: [fr(0.9), fr(1.2), fr(0.9)], rows: [fr(1), fr(1)], columnGap: 24, rowGap: 20 }, [
        card("North\ncallers / control", "Codex, Claude Code, CLI, hooks, HTTP, MCP；只传 task/session/outcome，不拥有 memory semantics。", C.blue, C.softBlue),
        panel({ fill: C.dark, line: { width: 0, fill: C.dark }, borderRadius: "rounded-md", padding: 24, width: fill, height: fill, rowSpan: 2 },
          column({ width: fill, height: fill, gap: 16, justify: "center" }, [
            tx("Core Knowledge Machinery", { style: { fontSize: 33, bold: true, color: "#FFFFFF", alignment: "center" } }),
            bullets(["typed lifecycle: source / procedure / decision / incident / concept", "archive-first intake + promotion queue", "trust / freshness / contradiction / negative knowledge", "policy-aware context packets", "outcome-linked validation + daemon maintenance"], 21, "#E9EEF7"),
          ]),
        ),
        card("East\nmemory products", "context packet, recommendation, warning, review queue, export；输出是带 policy 的产品，不是 ranked hits。", C.violet, C.softViolet),
        card("West\nraw intake", "sessions, PDFs, HTML, DOCX, Markdown, logs, web clips, code docs；MnemonicOS 拥有 projection 和 provenance。", C.cyan, C.softCyan),
        card("South\nsubstrates", "Markdown vault, SQLite, FTS, vectors, graph, object store；数据库和索引可替换，不是产品边界。", C.green, C.softGreen),
      ]),
      panel({ fill: C.paper, line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: { x: 24, y: 20 }, width: fill, height: grow(1) },
        row({ name: "architecture-pipeline-row", width: fill, height: fill, gap: 16, align: "center" }, [
          chip("Raw", C.softCyan, C.cyan, 110), arrow(),
          chip("Archive", C.softBlue, C.blue, 145), arrow(),
          chip("Projection", C.softAmber, C.amber, 170), arrow(),
          chip("Candidate", C.softGreen, C.green, 155), arrow(),
          chip("Canonical", C.softViolet, C.violet, 165), arrow(),
          chip("Packet", "#EEF0F5", C.ink, 130), arrow(),
          chip("Outcome", C.softRed, C.red, 140),
        ]),
      ),
    ]),
    C.green,
  );

  // 3 Processing logic
  slide(
    p,
    "PROCESSING LOGIC + RL",
    "处理核心是确定性的；哪些规则最有效，由 daemon / RL 在轨道内学习和固化",
    "我们不是让大模型随意写记忆，而是让小模型 daemon 学习结构化决策，并让 runtime/schema/rule 在评估中演进。",
    grid({ width: fill, height: grow(1), columns: [fr(1), fr(1)], columnGap: 42 }, [
      column({ width: fill, height: fill, gap: 18 }, [
        tx("确定性 runtime 负责边界", { style: { fontSize: 30, bold: true, color: C.ink } }),
        card("Runtime / schema / rule", "权限、类型、字段、promotion 状态、audit、rollback、deletion cascade、canonical write 都由工程规则执行。", C.blue, C.softBlue, { height: fixed(150), bodySize: 20 }),
        card("Daemon 负责决策建议", "score / suggest：extract、link、promote、defer、merge、flag stale、serve with warning、request review。", C.violet, C.softViolet, { height: fixed(150), bodySize: 20 }),
        card("LLM daemon 负责填充与解释", "从 raw / projection 中提取结构化内容，给出 proposal + explanation；runtime 决定是否落库。", C.green, C.softGreen, { height: fixed(150), bodySize: 20 }),
      ]),
      column({ width: fill, height: fill, gap: 18 }, [
        tx("可学习的闭环", { style: { fontSize: 30, bold: true, color: C.ink } }),
        panel({ fill: C.dark, line: { width: 0, fill: C.dark }, borderRadius: "rounded-md", padding: { x: 28, y: 26 }, width: fill, height: fixed(420) },
          column({ width: fill, height: fill, gap: 20, justify: "center" }, [
            row({ name: "learning-loop-row-1", width: fill, height: hug, gap: 12, align: "center" }, [chip("Raw + projection", "#26324A", "#FFFFFF", 220), arrow(), chip("Daemon proposal", "#26324A", "#FFFFFF", 230), arrow(), chip("Runtime gate", "#26324A", "#FFFFFF", 210)]),
            row({ name: "learning-loop-row-2", width: fill, height: hug, gap: 12, align: "center" }, [chip("Policy packet", "#26324A", "#FFFFFF", 210), arrow(), chip("Task outcome", "#26324A", "#FFFFFF", 210), arrow(), chip("Reward / preference", "#26324A", "#FFFFFF", 250)]),
            row({ width: fill, height: hug, gap: 12, align: "center", justify: "center" }, [tx("↺", { width: fixed(56), style: { fontSize: 48, bold: true, color: "#BFEAF0", alignment: "center" } }), tx("更新 action taxonomy / schema weights / promotion policy；高置信规则被固化进 runtime。", { style: { fontSize: 24, bold: true, color: "#E9EEF7" } })]),
          ]),
        ),
        card("避免不可收敛", "先冻结 schema v1 与 action taxonomy；先做 SFT / preference ranking；只在窄动作集合上做 offline RL；每次实验都能回滚到 deterministic baseline。", C.red, C.softRed, { height: grow(1), bodySize: 21 }),
      ]),
    ]),
    C.violet,
  );

  // 4 Steps
  slide(
    p,
    "THREE-STEP ROADMAP",
    "三个 step 的关系：先建 substrate，再证明 learned loop，最后才进入 neuro-symbolic core",
    "Step 2 是远大愿景，但必须由 Step 1 的数据、反馈闭环和 plateau 信号触发。",
    grid({ width: fill, height: grow(1), columns: [fr(1), fr(1), fr(1)], columnGap: 24 }, [
      stepPill("0", "True-Raw MemoryOS", "建立 raw intake、projection、typed lifecycle、policy packet、outcome loop、evaluation。目标是让 memory 具备可治理、可追溯、可训练的系统边界。", C.blue, C.softBlue),
      stepPill("1", "Learned Daemon", "训练 <8B 小模型 daemon 做结构化整理、分类、promotion、serving 策略建议；先 SFT / preference，再进入窄动作 offline RL。", C.violet, C.softViolet),
      stepPill("2", "Neuro-Symbolic Core", "当 Step 1 证明反馈闭环且粗粒度对象成为瓶颈后，重构为 observation / fragment / claim / tension / synthesis / model 的深层推理核心。", C.red, C.softRed),
    ]),
    C.amber,
  );

  // 5 Step 0
  slide(
    p,
    "STEP 0 BUILD",
    "Step 0 不是“再做一个 memory backend”，而是构建可被任何 agent 信任的 MemoryOS contract",
    "当前 repo 已有 local-first vault、runtime protocol、hybrid retrieval、trust/freshness、promotion queue、daemon maintenance；下一步是把它产品化为 true-raw substrate。",
    column({ width: fill, height: grow(1), gap: 20 }, [
      grid({ width: fill, height: fixed(420), columns: [fr(1), fr(1), fr(1), fr(1)], rows: [fr(1), fr(1)], columnGap: 18, rowGap: 18 }, [
        card("Raw vault", "append-only raw；content hash；source class；bridge identity；privacy class；deletion trail。", C.cyan, C.softCyan),
        card("Projection plugins", "session / Markdown / PDF / HTML first；DOCX / logs / source tree stretch；projection 可重建。", C.blue, C.softBlue),
        card("Typed lifecycle", "source / procedure / decision / incident / concept；draft / review / canonical / stale / contested。", C.green, C.softGreen),
        card("Promotion queue", "默认不直接写 canonical；daemon 解释 promote / defer / update / merge 的原因。", C.amber, C.softAmber),
        card("Freshness + trust", "freshness score、trust/usefulness、contradiction review、reverify / relearn tasks。", C.red, C.softRed),
        card("Policy packet", "retrieval_state、recommendation、verification_action、warnings、provenance、bounded context。", C.violet, C.softViolet),
        card("Outcome loop", "task_id 稳定；记录 success / failure / useful_memory / misleading_memory；反馈到维护。", C.blue, C.softBlue),
        card("Agent-independent", "Codex / Claude Code / CLI / HTTP / MCP 可共享一个 vault，但保留 bridge-scoped identity。", C.green, C.softGreen),
      ]),
      panel({ fill: C.dark, line: { width: 0, fill: C.dark }, borderRadius: "rounded-md", padding: { x: 28, y: 22 }, width: fill, height: grow(1) },
        column({ width: fill, height: fill, gap: 12 }, [
          tx("与竞品的关键差异", { style: { fontSize: 29, bold: true, color: "#FFFFFF" } }),
          bullets(["不是数据库：SQLite / vector / graph 是可替换 substrate", "不是 agent framework：agent 调用 MnemonicOS，但 memory contract 不属于某个 agent", "不是 CRUD memory：raw → projection → candidate → canonical → packet → outcome 是完整生命周期", "不仅记住成功：negative knowledge 与 misleading memory 是一等对象"], 22, "#E9EEF7"),
        ]),
      ),
    ]),
    C.cyan,
  );

  // 6 RL
  slide(
    p,
    "STEP 1 RL DESIGN",
    "用 <8B learned daemon 做结构化整理和策略选择，而不是训练一个“懂一切”的大模型外挂",
    "这个 core 不需要理解所有业务内容；它需要学习 MemoryOS 内部的结构性整理、有效分类、更新策略和 serving 风险控制。",
    grid({ width: fill, height: grow(1), columns: [fr(1), fr(1)], columnGap: 42 }, [
      column({ width: fill, height: fill, gap: 18 }, [
        tx("输入 → 输出", { style: { fontSize: 30, bold: true, color: C.ink } }),
        panel({ fill: C.paper, line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: 24, width: fill, height: fixed(255) },
          column({ width: fill, height: fill, gap: 12 }, [
            tx("输入", { style: { fontSize: 28, bold: true, color: C.blue } }),
            bullets(["raw projection spans + metadata", "current canonical objects + freshness/trust", "retrieval packet + task intent + token budget", "outcome traces: useful / misleading / retry / success"], 20),
          ]),
        ),
        panel({ fill: C.paper, line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: 24, width: fill, height: fixed(255) },
          column({ width: fill, height: fill, gap: 12 }, [
            tx("输出", { style: { fontSize: 28, bold: true, color: C.violet } }),
            bullets(["action suggestion + score + rationale", "candidate object fields / links / type labels", "serve format: concise / example-heavy / warning-first / provenance-first", "schema/rule change proposal for offline evaluation"], 20),
          ]),
        ),
      ]),
      column({ width: fill, height: fill, gap: 18 }, [
        tx("训练与收敛策略", { style: { fontSize: 30, bold: true, color: C.ink } }),
        panel({ fill: C.dark, line: { width: 0, fill: C.dark }, borderRadius: "rounded-md", padding: 26, width: fill, height: fixed(330) },
          column({ width: fill, height: fill, gap: 18, justify: "center" }, [
            row({ name: "rl-training-row", width: fill, height: hug, gap: 8, align: "center" }, [chip("Heuristic logs", "#26324A", "#FFFFFF", 180), arrow(), chip("SFT", "#26324A", "#FFFFFF", 90), arrow(), chip("Preference", "#26324A", "#FFFFFF", 160), arrow(), chip("Offline RL", "#26324A", "#FFFFFF", 145)]),
            tx("Reward = downstream usefulness + compactness + provenance correctness + freshness safety + schema validity", {
              style: { fontSize: 26, bold: true, color: "#FFFFFF" },
            }),
          ]),
        ),
        grid({ width: fill, height: grow(1), columns: [fr(1), fr(1)], columnGap: 18, rowGap: 18 }, [
          card("小模型定位", "<8B；本地/低成本；只学 memory operations，不承担最终任务推理。", C.green, C.softGreen, { bodySize: 20 }),
          card("比 regex 更 adaptive", "面对不稳定输入时能做分类、聚合、链接、风险判断；但写入仍受 runtime gate 控制。", C.blue, C.softBlue, { bodySize: 20 }),
          card("避免变成大模型补丁", "训练目标不追求回答任务，而追求可复用的 MemoryOS action policy。", C.violet, C.softViolet, { bodySize: 20 }),
          card("避免不可收敛", "窄动作集合、强 baseline、离线 ablation、可回滚 schema/rule proposals。", C.red, C.softRed, { bodySize: 20 }),
        ]),
      ]),
    ]),
    C.violet,
  );

  // 7 Roadmap
  slide(
    p,
    "6-MONTH PLAN + LABOR",
    "6 个月目标：完成 Step 0 substrate MVP，并交付 Step 1 learned daemon proof",
    "计划按 milestone gate 推进；建议预算约 24 人月，用于 2-3 名工程/研究人员并行推进。",
    column({ width: fill, height: grow(1), gap: 24 }, [
      grid({ width: fill, height: fixed(260), columns: [fr(1), fr(1), fr(1), fr(1), fr(1), fr(1)], columnGap: 12 }, [
        card("M1\nSpec + vault", "设计冻结；raw model；intake API；fixture corpus。", C.blue, C.softBlue, { titleSize: 28, bodySize: 18 }),
        card("M2\nProjection", "plugins；candidate pipeline；source/procedure/decision fields。", C.cyan, C.softCyan, { titleSize: 28, bodySize: 18 }),
        card("M3\nServing loop", "policy packet；stable IDs；outcome logs；trace export。", C.green, C.softGreen, { titleSize: 28, bodySize: 18 }),
        card("M4\nEval gate", "LongMemEval/LoCoMo slice；MnemonicOS-specific eval；baselines。", C.amber, C.softAmber, { titleSize: 28, bodySize: 18 }),
        card("M5\nSFT daemon", "action policy imitation；promotion/serve-format suggestions。", C.violet, C.softViolet, { titleSize: 28, bodySize: 18 }),
        card("M6\nPreference demo", "preference/ranking；ablation；integrated true-raw demo。", C.red, C.softRed, { titleSize: 28, bodySize: 18 }),
      ]),
      grid({ width: fill, height: grow(1), columns: [fr(1), fr(1), fr(1)], columnGap: 24 }, [
        panel({ fill: C.paper, line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: 24, width: fill, height: fill },
          column({ width: fill, height: fill, gap: 12 }, [
            tx("Labor estimate", { style: { fontSize: 30, bold: true, color: C.ink } }),
            bullets(["Base: 2 FTE × 6 months = 12 person-months", "Recommended: 3-4 FTE equivalent = ~18-24 person-months", "Why: projection, eval, runtime hardening, daemon training can parallelize"], 21),
          ]),
        ),
        panel({ fill: C.paper, line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: 24, width: fill, height: fill },
          column({ width: fill, height: fill, gap: 12 }, [
            tx("Milestone gates", { style: { fontSize: 30, bold: true, color: C.ink } }),
            bullets(["M3: every task can answer what memory was served and why", "M4: show MemoryOS value beyond retrieval delta", "M6: heuristic vs SFT vs preference daemon ablation"], 21),
          ]),
        ),
        panel({ fill: C.dark, line: { width: 0, fill: C.dark }, borderRadius: "rounded-md", padding: 24, width: fill, height: fill },
          column({ width: fill, height: fill, gap: 12 }, [
            tx("Final demo", { style: { fontSize: 30, bold: true, color: "#FFFFFF" } }),
            bullets(["raw intake → projection → governed memory", "policy-aware serving → outcome feedback", "daemon proposals → runtime-gated writes", "RL story: outcome-driven memory lifecycle improvement"], 21, "#E9EEF7"),
          ]),
        ),
      ]),
    ]),
    C.amber,
  );

  // Backup A
  slide(
    p,
    "BACKUP A",
    "竞争定位：不要把 slow promotion 当唯一差异，真正差异是完整 operating layer",
    "来自 MnemonicOS Competitive Landscape：Honcho / OpenClaw 已有 consolidation/review overlap，因此主张要转向 policy packet、outcome feedback、raw intake、agent-independent runtime。",
    grid({ width: fill, height: grow(1), columns: [fr(1), fr(1), fr(1)], columnGap: 24 }, [
      card("已有 overlap", "Honcho Dreaming、OpenClaw dreaming/wiki、Mem0 typed labels、Zep/Graphiti temporal graph 都覆盖部分 memory 能力。", C.amber, C.softAmber, { bodySize: 22 }),
      card("MnemonicOS moat", "double-linked provenance：raw-source spans + success/failure traces；negative knowledge；cross-agent shared vault。", C.blue, C.softBlue, { bodySize: 22 }),
      card("产品类别", "不是框架库、数据库、embedding service 或 agent；而是任何 agent 都能调用的 local-first memory runtime。", C.green, C.softGreen, { bodySize: 22 }),
    ]),
    C.muted,
  );

  // Backup B
  slide(
    p,
    "BACKUP B",
    "风险与对策：把风险设计进 runtime，而不是交给 prompt 运气",
    "",
    grid({ width: fill, height: grow(1), columns: [fr(1), fr(1)], columnGap: 28, rowGap: 20 }, [
      card("Raw intake 风险", "噪声、隐私、投毒、版权：privacy class、deletion trail、audit、projection isolation 从 day 1 做。", C.red, C.softRed, { bodySize: 21 }),
      card("Reward 延迟和噪声", "显式记录 useful / misleading / retry / task success；先 preference ranking，再窄动作 RL。", C.violet, C.softViolet, { bodySize: 21 }),
      card("Benchmark delta 小", "不要只押 retrieval；同步评估 provenance correctness、stale/misleading avoidance、policy-packet usefulness。", C.blue, C.softBlue, { bodySize: 21 }),
      card("Daemon 过拟合 heuristic", "改 trace quality、action taxonomy 和 contrastive examples；保留 deterministic baseline。", C.green, C.softGreen, { bodySize: 21 }),
    ]),
    C.muted,
  );

  return p;
}

async function main() {
  for (const name of await readdir("scratch")) {
    if (/^(preview-slide-|layout-slide-|montage|pptx-roundtrip)/.test(name)) {
      await unlink(`scratch/${name}`).catch(() => {});
    }
  }
  const p = buildDeck();
  for (let i = 0; i < p.slides.count; i += 1) {
    const s = p.slides.getItem(i);
    const png = await p.export({ slide: s, format: "png" });
    await writeFile(`scratch/preview-slide-${String(i + 1).padStart(2, "0")}.png`, Buffer.from(await png.arrayBuffer()));
    const layout = await p.export({ slide: s, format: "layout" });
    await writeFile(`scratch/layout-slide-${String(i + 1).padStart(2, "0")}.json`, Buffer.from(await layout.arrayBuffer()));
  }
  const pptx = await PresentationFile.exportPptx(p);
  await pptx.save("output/output.pptx");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
