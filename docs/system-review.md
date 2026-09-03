# FinScope 系统体检报告

> 体检范围：`src/graphs` / `src/agents` / `src/extractors` / `src/utils` / `src/tools` / `tests`
> 方法：逐文件读源码，所有结论附 `文件:行号` 证据，不做印象式判断。
> 日期：2026-09-03

---

## 〇、两条架构决策（本次体检确立，后续所有设计以此为准）

### 决策 1：保持 agent 形态，不做「确定性路由」

我最初建议「Supervisor 改用确定性状态机，去掉 LLM 路由」，**该建议已撤回**。

| | 性质 | LLM 是否参与 |
|---|---|---|
| **合规判定** | 判定结论、决定是否放行 | **零参与**（红线） |
| **任务调度** | 协调谁下一个干活 | **参与**——协调能力，不是判定能力 |

我把「确定性引擎」与「确定性路由」混为一谈，扩大了红线的适用范围。

**现状已满足红线**：`RegulationEngine` + `ContentFilter` 均为确定性规则，且终态闸门跑在 agent 循环**之外**（`financial_graph.py:343`、`:404`），位置正确。
→ **调度归 LLM（保持 agent 形态），合规归规则（红线不动）。**

**但保持形态的前提是把 agent 做实**（见 §一 问题 ②）：当前 agent 无工具、单次调用，是「付 agent 成本、没拿 agent 收益」。

### 决策 2：合规红线边界澄清（记录备查）

- ✅ 已落实：合规判定 LLM 零参与；终态闸门在循环外
- ⚠️ 待确认：终态闸门是「脱敏放行 + CRITICAL 横幅告警」策略（`financial_graph.py:204-214`），
  对内部 analyst 工具合理，但需业务方签字确认接受「放行」而非「拦截」

---

## 一、P0 — 结构性缺陷（必修）

### ① 不传 PDF 时整条链路残废：缺「公司名 → 股票代码」解析

**证据链**：
```
report_extractor.py:44-51   无 PDF → 直接 return，extracted_entities 必空
data_retriever.py:73-76      兜底只在 user_query 里正则找 6 位代码
data_retriever.py:78-81      仍找不到 → 直接跳过，financial_data 永远为空
```

**后果**：用户问「分析复星医药」（不带 6 位代码）→ 正则匹配不到 → 市场数据全程缺失 → Analyst 只能对着空数据输出。

**修法**：akshare `stock_info_a_code_name()` 提供全 A 股代码↔名称映射，加一个 name→code 解析环节即可，成本极低。

---

### ② agent 形态「空壳化」：无工具、无自主性

**证据**：
- 5 个 agent 各自**只有一次 LLM 调用**（如 `financial_analyst.py:172`）
- 工具全部由 Python 直调，不在 agent 手上（`data_retriever.py:91-92`、`report_extractor.py:62`）
- Supervisor 按固定顺序路由（`supervisor.py:139-145` 的表格本质就是固定流水线）

**判断**：这是「每节点一个 prompt 的状态机」，不是自主多智能体。
保持形态可以，但**必须补上工具能力**——否则是在付 agent 的成本（LLM 路由调用、不确定性），却没拿到 agent 的收益（自主取数、按需深挖）。

**修法**：见 `implementation-plan.md` P2.2（有界工具循环 + 工具审计 + 工具预算）。
附带收益：agent 按需取数后，**上下文溢出问题从架构上被缓解**——不再是「预先全量注入」。

---

### ③ 无重试/退避，网络抖动直接失败

**证据**：
- `llm_client.py:40` `max_retries=1`
- `safe_invoke` 捕获异常后**直接返回 error dict**（`:128-153`），不做任何重试

**后果**：429 / 超时 / 瞬时网络抖动一律当场失败。银行内部网络环境抖动是常态。

**修法**：对 `rate_limit` / `timeout_error` / `network_error` 三类加指数退避重试（3 次），`auth_error` 不重试。

---

### ④ 修订循环白烧 token：未启用 prompt caching

**证据**：`financial_analyst.py:157` 每轮 revise 都重新注入完整 `analysis_input`（可达数万 token）。

**判断**：修订循环是 prompt caching 的**完美命中场景**（前缀稳定 + 重复调用）。DeepSeek 支持 caching。
当前 revise 最多 3 轮 → 约 3 倍 token 与延迟浪费。

**优先级**：全场性价比最高的一处优化。且**保留 agent 形态后更急**——LLM 调用次数更多。

---

## 二、P1 — 明显不足

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| ⑤ | **成本/耗时零观测** | `llm_client.py:126` 取了 `usage`，但全项目无任何汇总 | 跑一次分析花多少 token、哪步慢，完全不可知 |
| ⑥ | **max_tokens 全局单一值 4096** | `config.py:82`；`llm_client.py:37` 所有调用共用 | Analyst 写 5 维度分析、Writer 写完整报告、Supervisor 只输出一行 JSON 共用一个上限 → 长报告**被截断且无告警** |
| ⑦ | **无端到端测试** | `tests/` 8 文件 43 条，全是 fixture 单测 | 没有一条跑通完整图；改压缩层后无法验证端到端是否还 work |
| ⑧ | **source tracing 半吊子** | prompt 要求标 `[P 页码]`（`financial_analyst.py:128-131`）但**无校验** | 模型写 `[P 999]` 也照样输出；前端无高亮回溯 |
| ⑨ | **`pdf_sections[:30]` 硬截断** | `financial_analyst.py:47` | 100 页研报后 70 页直接丢，且注入前不算 token |
| ⑩ | **流式只在节点粒度** | agent 内用同步 `safe_invoke`（`financial_analyst.py:172`）；`safe_astream` 存在但没用 | 2 分钟分析期间 UI 无任何反馈 |

---

## 三、P2 — 打磨项

| # | 问题 | 证据 |
|---|---|---|
| ⑪ | `summary = full_text[:2000]` 是死字段 | `entity_extractor.py:111`（无任何消费方） |
| ⑫ | 熔断耦合不直观 | `MAX_ITERATIONS=15`（`config.py:74`）是「调度轮数」不是「修订轮数」：正常 5 轮 + 每轮 revise 3 轮 → 最多 3 次 revise 即熔断，此时**报告可能还没写完**（`financial_graph.py:228-229` 直接 END） |
| ⑬ | 两份重复的指标正则 | `entity_extractor.py:66-74` 与 `page_compressor.py:250-269` 各存一份 7 类指标正则 |

---

## 四、处理去向

| 问题 | 去向 |
|---|---|
| ⑨ `[:30]` 截断、上下文溢出 | **`implementation-plan.md` P1 + P2 解决**（本次范围） |
| ② agent 空壳化 | **`implementation-plan.md` P2.2 解决**（工具化，本次范围） |
| ⑧ 引用校验 | P2 工具 `verify_citation` 具备基础能力，完整校验留后续 |
| ⑬ 正则重复 | P1 收敛为单一副本（顺带） |
| ①③④⑤⑥⑦⑩⑪⑫ | **独立排期**，见下 |

### 独立项建议顺序（不阻塞压缩主线）

1. **④ prompt caching**（半天，收益最大）
2. **③ 重试退避 + ⑤ 成本观测**（一天，运维地基）
3. **① 公司名→代码**（半天，补功能洞）
4. **⑦ 端到端测试**（一天，后续所有改动的安全网）
5. ⑥ max_tokens 分项配置 / ⑩ 真流式 / ⑪⑫ 打磨

---

## 五、保持 agent 形态要多付的两笔账（先记账）

1. **工具调用必须审计留痕**——agent 自主调用工具比 Python 直调更难追，需 `tool_call_history` 记全：谁、何时、调了什么、参数、返回体量、耗时。
2. **必须给 agent 工具调用预算**——agent 可能循环调工具，需 `MAX_TOOL_CALLS_PER_AGENT` + 超时熔断，否则单次分析账单不可控。

两条已并入 `implementation-plan.md` P2.2 设计。
