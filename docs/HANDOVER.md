# FinScope 交接文档

> 最后更新：2026-09-03（P1→P3 完成，P4 待开工）
> 阅读顺序：本文档（总览）→ `p4-backlog.md`（待办）→ `implementation-plan.md`（实施细节）
> → `compression-plan.md`（架构论证）→ `system-review.md`（系统体检）

---

## 1. 项目一句话

**面向银行内部分析师/研究员的「财报分析多 Agent 系统」**：上传年报/研报 PDF，
Supervisor 调度多个 agent 完成抽取 → 分析 → 撰写 → 复核，产出可溯源的研究报告。

**唯一目标**：让每个 agent 都能在**上下文预算内**拿到它需要的年报知识并完成分析。
L1 / L2 / L3 / 装配器 / 工具 / facts 全部是**手段**，不是目标。

---

## 2. 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P1** | L1 结构化无损层（章节/表格/事实） | ✅ 完成 |
| **P2** | 预算装配 + Agent 工具化 | ✅ 完成 |
| **P3** | 章节级 L2 / L3 + 装配接入 | ✅ 完成 |
| **P4** | Writer/Reviewer 接入 + 开关翻转 + 删旧 + 端到端验收 | ✅ 完成（B1–B5 / A1–A5 全 ✅） |

**测试：145 条全绿**（含 B 组 21 + A1 7 + A2 7 + A3/A4 5 + 工具循环 1 + 真实年报 8 + C2 4 + C4 2）

> 2026-09-03 收口：**P4 全部完成**（B1–B8 / A1–A5 全 ✅）。4/4 agent 持工具；双轨付费关闭；
> 真实 DeepSeek 端到端验收通过（joinn 189 页年报：6/6 agent done、87 处页码引用、事实抽检
> 10/10、修订循环真实发生、Reviewer 真实判定）。验收修了三个真 bug：langchain-openai
> 版本冲突（环境）、**工具循环轮数耗尽无产出**（强制总结轮兜底——llm_client）、元评论
> 前缀泄漏（Writer 剥离至 H1 + Reviewer JSON 大括号切片）。验收脚本
> `scripts/run_e2e_a5.py`（seed 种子化 MinerU 产物 → parse_cache；run 跑全链路出指标）。
> `.env` 已配置（DeepSeek，key 来自 ~/.lite-ai/settings.json）。
>
> 2026-09-03 追加：**B6/B7/B8 完成**（抽取后校验规范化层补齐）：
> B6 科目别名表（`metric_std` + dedup 归一 + query_fact 查询侧）；B7 勾稽校验
> （`identity_checker.py`，joinn+furui 两份真实年报 4 项恒等式全 PASS）；B8 单位三级解析
> （caption → 邻接文本 → 网格单位行，furui 301/393 表、joinn 26 表）。顺带修
> `parse_number` 数字内部空格（MinerU OCR 伪影，furui 多抽 31 条 facts）。
> B 组验收脚本 `scripts/check_b678_real.py`（两份年报 B6/B7/B8 指标）。
> MinerU 解析数据：joinn（港股繁体）与 furui（A 股简体，`m1/out/furui_v2/`）双样本。
> 遗留见 backlog C/D 组：C1 查询相关性排序、akshare 数据源不稳、LLM 调用计量、C4 语义。
>
> 2026-09-03 C 组首批完成（C2/C4/C5，零 LLM 标定实验 + 真实 E2E 复验）：
> **C2 抓到架构级缺陷并修复**——文档序直注吃光预算导致 L2 注入率 0-5%（P3 设计意图
> 实际未发生），`context_assembler` 加 L2 预留配额（15%/封顶 4k）后 furui 0→6、
> joinn 3→13 注入，预算利用率 98%，E2E 复验无质量回归（溯源 10/10）。**C4** 工具上限
> 改名 `MAX_TOOL_ROUNDS_PER_AGENT` + 真实调用数硬熔断 40。**C5** 数据否定预算上调
> （36k 后直注饱和，48k 零增益），保持 24k。A 股 furui 定为主验收样本、joinn 为回归
> 护栏。Reviewer 静默放行 bug 已修（schema 强约束重试 → 可见降级）。

---

## 3. 架构全景

```
PDF ──► [ReportExtractor·确定性]
          ├─ MinerU 全文解析（API 服务优先，本地 CLI 降级）
          ├─ 实体抽取（纯正则）
          ├─ L1 构建（章节/表格/事实，零 LLM）──┐
          └─ 旧压缩 compress_pages（P4 删）      │ 落 parse_cache（SHA-256，schema v2）
                                                 ▼
        ┌─────────────── pdf_l1 ────────────────────────┐
        │  [P3] L2 惰性构建（T1/T2 大章节，跨轮缓存）  │
        │  [P3] L3 全局亮点（确定性规则版）            │
        ▼                                               │
   [Context Assembler·预算驱动]  ←──────────────────────┘
        │  L3 → L1 按文档序贪心直注 → 溢出注 L2 → 剩余落指针
        ▼
   pdf_context ──► [Analyst ✅ / Writer ✅(A1) / Reviewer ✅(A2)]
                       └─ 工具：fetch_context / query_fact / search_section
                          有界循环 + 每次调用写 tool_call_history（审计）
```

**节点性质**（合规架构：确定性引擎 + LLM 受限叶子，判定归规则）：

| 节点 | 性质 | 说明 |
|---|---|---|
| Supervisor | ✅ agent | LLM 路由（协调能力，非合规判定） |
| ReportExtractor | 确定性节点 | 解析 + 实体 + L1（**应当**确定性，可缓存可复现） |
| DataRetriever | 确定性节点 | akshare/tushare 取数 |
| FinancialAnalyst | ✅ agent | **已接** pdf_context + 3 工具 |
| ReportWriter | ✅ agent | **已接**（A1）— flag 开启走 pdf_context + 3 工具 |
| Reviewer | ✅ agent | **已接**（A2）— flag 开启 + facts 就绪走数字核验（query_fact 核对 + 页码校验） |

> ⚠️ 现状：**4 个真 agent 已全部持有工具**（Analyst/Writer/Reviewer），flag 默认仍关（A4 翻转）。

---

## 4. 三条不可动摇的决策

1. **保持 agent 形态**（用户拍板）：Supervisor 的 LLM 路由保留。
   —— 我曾建议改确定性路由，**已撤回**：合规判定归 LLM 零参与，任务调度是协调能力，两者不能混。
2. **P1–P4 全做**（用户拍板）：P3 对年报是**刚需不是优化**——实测 24k 预算下 536 章只有 ~34 章装得下，
   没有 L2 会退化成「指针汤」。
3. **合规红线**：合规判定 LLM 零参与；终态闸门跑在 agent 循环之外（`financial_graph.py:343`/`:404`）。

---

## 5. 已交付文件

### P1（L1 结构化）
| 文件 | 说明 |
|---|---|
| `src/extractors/section_segmenter.py` | **新增** 切章节 + 跨页续节合并 + T0–T3 纯规则分档（带 `tier_reason`） |
| `src/extractors/table_serializer.py` | **新增** lxml 展开 rowspan/colspan + Markdown 分块（每块重复表头） |
| `src/extractors/fact_extractor.py` | **新增** 表格定位(0.9) + 正文正则(0.6)，去重 + 溯源 |
| `src/extractors/l1_builder.py` | **新增** 编排产出 `{sections, tables, facts, stats}` |
| `src/extractors/mineru_extractor.py` | 改：heading 归一化 + 页眉/页脚/页码入口丢弃 |
| `src/tools/financial_tools.py` | 改：L1 与解析结果同落缓存 |
| `src/extractors/parse_cache.py` | 改：`SCHEMA_VERSION` 1→2 |
| `src/extractors/page_compressor.py` | 改：兼容新 heading 类型（旧缓存可读，P4 删） |
| `tests/test_l1_pipeline.py` / `test_l1_real_joinn.py` | 29 + 7 条 |

### P2（装配 + 工具化）
| 文件 | 说明 |
|---|---|
| `src/utils/token_counter.py` | **新增** tiktoken cl100k + 15% 余量，失败降级字符估算 |
| `src/extractors/context_assembler.py` | **新增** 预算装配，硬约束 `used ≤ budget` 断言 |
| `src/agents/context_tools.py` | **新增** 工具工厂 → fetch_context / query_fact / search_section |
| `src/utils/llm_client.py` | 改：`safe_invoke_with_tools`（bind_tools + 有界循环 + 审计回调） |
| `src/agents/financial_analyst.py` | 改：flag 开启走新链路，**关闭时旧路径原样**（防回归） |
| `src/graphs/state.py` | 改：`pdf_l1/pdf_l2/pdf_l3/pdf_context` |
| `src/utils/config.py` | 改：`USE_MULTILEVEL_COMPRESSION` / `CONTEXT_BUDGET_TOKENS` / `MAX_TOOL_CALLS_PER_AGENT` |

### P3（L2/L3）
| 文件 | 说明 |
|---|---|
| `src/extractors/section_compressor.py` | **新增** 章节 L2（LLM + 规则兜底）+ 增量缓存 + L3-lite 规则亮点 |
| `src/extractors/context_assembler.py` | 改：接 l2/l3；**排序改文档序优先**；T0 散文封顶；超长散文优先 L2；指针封顶+汇总脚注 |
| `src/agents/financial_analyst.py` | 改：装配前惰性构建 L2/L3，写回 `pdf_l2/pdf_l3` |
| `tests/test_p3_compressor.py` | 8 条（含 2 真实年报） |

---

## 6. 真实数据实证（joinn 2024 年报，H 股繁体）

来源：`D:\develop\财报分析助手\m1\out\joinn_v3\joinn_2024_annual\auto\joinn_2024_annual_content_list.json`

| 指标 | 数值 |
|---|---|
| 页数 / 原始 item | 189 / 2256（text 1699, table 115, **header 50=页眉噪声**, footer 192, page_number 199） |
| 真标题 | `type=text` **且** `text_level>0` → 561 个 |
| L1 构建耗时 | **0.15s**（零 LLM） |
| 章节 | 536（T0 145 / T1 26 / T2 361 / T3 4），跨页续节已合并 |
| 表格 | 111（4 个空体剔除），解析失败 **0** |
| 事实 facts | **451 条**，全部表格定位（0.9），带完整溯源 |
| 装配 @24k 预算 | raw 55 + L2 6 = **61 章**注入，used ~23.5k，指针 471 |
| 装配 @8k 预算 | raw 23 + L2 3 |

事实样例：
```
昭衍新药 | 非臨床研究服務 | FY2024 = 1,917,487 | src p21 t_005 row3 col"2024年"
```

---

## 7. 已知限制（诚实清单，勿当成已完成）

### facts 质量（B 组：B1–B5 ✅ 已修，B6–B8 待办）
- ~~**B1 编号行误抽**~~ ✅、~~**B2 会计括号负数**~~ ✅、~~**B3 % 量纲剥离**~~ ✅、~~**B4 合计/小计行**~~ ✅、~~**B5 期间精度**~~ ✅（2026-09-03，见 `p4-backlog.md` 验收记录）
- **B6 科目未标准化**：`非臨床研究服務` ≠ `营业收入`（LLM 别名归一默认关）
- **B7 无勾稽校验**（资产=负债+权益）
- **B8 unit 基本为空**：多数 MinerU caption 为空，裸大数（整章正则启发误命中已回退，宁缺毋滥）

### 工程侧（C 组）
- 装配排序是启发式（文档序），**query 相关性重排未做**
- L2 压缩强度未标定；**LLM 版 L2 质量未验**（规则兜底已验）
- `MAX_TOOL_CALLS_PER_AGENT` 是**LLM 轮数**上限，非调用数（最坏 ≈ 轮数×2）
- `CONTEXT_BUDGET_TOKENS=24000` 偏保守（DeepSeek chat 窗口 64k，可上调 ~48k）
- **只在 1 份繁体 H 股年报验证**，研报/招股书形态需再验
- **双轨付费**：flag 默认关，旧 eager 压缩仍在跑（A4 关闭）

---

## 8. 别踩的坑（本轮实证教训）

1. **MinerU 字段语义别照文档猜**：本样本 `type=header` 是**页眉噪声**，真标题是 `type=text` + `text_level>0`。
   照文档写会把噪声当标题、丢掉全部真标题（双重错误）。
2. **prompt 模板里的 JSON 要转义**：`{"thesis"...}` 会被 `.format()` 当占位符 → `KeyError: '"thesis"'`，必须写 `{{}}`。
3. **指针行也占预算**：数百个溢出章节逐条列指针会烧掉 4–5k token，挤掉 L2 摘要 → 封顶 + 汇总脚注。
4. **tier 优先排序在年报上是错的**：145 个 T0 附注（p124+）会吃光预算，p26 管理层讨论排不上 → 改**文档序优先**。
5. **中文字符截断要按 ~1.5 token/字迭代收敛**，别用英文的 4 字符/token 假设。
6. **「零 LLM」断言别用 `sys.modules`**：全量套件里其他用例会先导入 `utils.llm_client` 造成污染误报 → 用源码级静态检查。
7. **单位启发式宁缺毋滥**：整章正则探测把千元表误标「百萬元」（1,917,487 千元≈19.17亿 → 标百萬 = 1.9万亿，荒谬）。
8. **缓存语义**：`max_new` 熔断下第二轮是「继续建下一批」而非「零增量」，验收要先建满再断言第二轮为空。

---

## 9. 怎么跑

环境：项目 Python 311（akshare / lxml / tiktoken 装在这里）
```
C:\Users\Queenie\AppData\Local\Programs\Python\Python311\python.exe
```

```bash
# 全量测试（98 条）
cd D:\develop\财报分析系统
<py> -m pytest tests/ -q

# 只跑 L1/P2/P3 新增
<py> -m pytest tests/test_l1_pipeline.py tests/test_l1_real_joinn.py tests/test_p2_context.py tests/test_p3_compressor.py -q

# 真实年报验收（样本缺失时自动 skip）
<py> -m pytest tests/test_l1_real_joinn.py tests/test_p3_compressor.py -q
```

关键配置（`src/utils/config.py`，均可 `.env` 覆盖）：
```
USE_MULTILEVEL_COMPRESSION = False   # 灰度开关，P4 翻 True
CONTEXT_BUDGET_TOKENS      = 24000   # 装配硬约束
MAX_TOOL_CALLS_PER_AGENT   = 5       # LLM 轮数上限
MAX_L2_BUILD_PER_RUN       = 8       # 单次新增 L2 章数
L2_MIN_TEXT_TOKENS         = 600     # 值得压缩的最小章节体积
```

---

## 10. 下一步

按 `p4-backlog.md` 顺序：

```
A1 Writer 接 pdf_context + 工具
  → A2 Reviewer 用 query_fact 核验数字（+ verify_citation 页码真实性）
  → A3 多 agent 共用 pdf_l2
  → A4 开关翻 True + 删 page_compressor / pdf_sections / pdf_summary
  → A5 端到端验收（真实年报 + 修订循环第二轮增量 token）
  → B6~B8 → C 组 → D 组
```

（B1–B5 已于 2026-09-03 清完）

**D 组（独立优化，与 P4 解耦）**：prompt caching（性价比最高）、重试退避 + 成本观测、
公司名→股票代码、端到端测试、max_tokens 分项、真流式、`MAX_ITERATIONS` 语义、`summary` 死字段。

---

## 11. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/HANDOVER.md` | 本文：总览与交接 |
| `docs/p4-backlog.md` | P4 问题清单（A/B/C/D 四组，按编号推进） |
| `docs/implementation-plan.md` | 实施计划 v4（P1–P4 文件清单、数据结构、验收标准） |
| `docs/compression-plan.md` | 多级压缩架构论证（L0–L3、T0–T3、装配算法、§5.3 悬空指针论证） |
| `docs/system-review.md` | 系统体检报告（13 处问题 + 2 条架构决策 + 独立项排期） |
