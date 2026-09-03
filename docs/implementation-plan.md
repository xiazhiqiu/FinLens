# FinScope 上下文压缩落地计划 v4（全量 P1–P4）

> 配套文档：
> - `docs/compression-plan.md` —— 架构设计与论证（L0–L3 / T0–T3 / 装配算法）
> - `docs/system-review.md` —— 系统体检报告（13 处问题 + 两条架构决策）
>
> **本次范围**：**P1 → P4 全量落地**（v3 曾把 P3/P4 降为条件性「纯优化」，经复查撤回，理由见 §〇）。
> 各阶段仍串行推进、独立验收，不是一次性大爆炸。
>
> 立场：以「合理」为唯一标准，不被现有代码约束；允许重构、重命名、删除、加依赖。

---

## 〇、范围决策：为什么全做（v3 收敛被撤回的理由）

v3 曾把 P3/P4 定为「条件性后续、纯优化」，经复查**这个判断错了两处**：

| # | v3 判断 | 复查结论 |
|---|---|---|
| 1 | P3 是纯优化，P2 够用就不做 | **错**。主场景是**年报（100–200 页）**，L1 全量约 50k–150k token，24000 预算必然装不下。没有 P3，长文档退化成「指针汤」——大多数章节只剩 `fetch_context` 指针，agent 手动回取还受 `MAX_TOOL_CALLS_PER_AGENT=5` 约束，根本取不完。**P3 对年报是刚需，不是优化** |
| 2 | P4 是迁移清理，可以缓 | **错**。P4 完成前旧链路（eager 逐页 LLM 压缩）仍在运行填充 `pdf_sections`/`pdf_summary`，等于**新旧两套同时付费**。「省掉全部压缩成本」的收益要到 P4 删掉旧路径才兑现。P2→P4 之间是**双成本窗口**，拖得越久烧得越多 |

**修正后的执行策略**：全量 P1→P4，串行推进，每阶段独立验收。
真正「按需再定」的只剩一个参数：**L2 压缩强度**（压多狠）用真实年报标定，不拍脑袋。

**附带解决的体检问题**：⑨ `pdf_sections[:30]` 硬截断、② agent 空壳化（工具化）、⑬ 两份重复正则（收敛）。
**不解决的**（独立排期）：①③④⑤⑥⑦⑩⑪⑫ —— 见 `system-review.md` §四。

---

## 一、P1 地基（确定性，零 LLM）

### 1.0 技术选型（加依赖）

| 依赖 | 用途 | 必要性 |
|---|---|---|
| `lxml>=5.2` | 表格 HTML → 结构化网格（展开 rowspan/colspan、容错不规范标签） | 核心链路精度 |
| `tiktoken>=0.8` | 精确 token 计数（cl100k_base） | 预算驱动的正确性前提 |

> DeepSeek 分词与 cl100k 有偏差 → 装配器统一加 15% 安全余量 + 硬上限兜底。

### 1.1 heading 归一化（改 `mineru_extractor.py`）

MinerU 各版本 heading 字段拼写不一致（`title` / `header` / `heading`），部分版本用 `text_level` 标记层级。
现有代码只认 `type == "header"`（`mineru_extractor.py:302-310`），遇到 `title` 会**静默丢弃整条 item**——不报错、不告警，切分器一个章都切不出来。

```python
_HEADING_TYPES = {"title", "header", "heading"}

def _normalize_item_type(item) -> str:
    t = item.get("type", "text")
    if t in _HEADING_TYPES or item.get("text_level", 0) > 0:
        return "heading"
    return t if t in ("text", "table", "image") else "text"
```
输出统一为 `{"type": "heading", "level": N}`，下游只认一种。

### 1.2 新增 `src/extractors/section_segmenter.py`

```python
Section = {
    "section_id": "s_003",
    "title": "财务表现",
    "level": 2,
    "page_range": [12, 14],
    "tier": "T0",
    "tier_reason": "命中T0关键词:利润表",   # 判定理由，审计可回放
    "text": "...",
    "table_ids": ["t_04"],
}

def segment_sections(structured_pages) -> List[Section]
def classify_tier(title, text, has_table, pos_hint) -> Tuple[str, str]
```

**切分**：遇 `type == "heading"` 开新章；`table` 挂 `table_ids`；`text` 追加正文。
**无 heading 的文档**（md 降级单页）→ 整篇作单章，不崩。

**T0–T3 判定**（纯规则，每 Section 必写 `tier_reason`）：
```python
T0_KW = ["资产负债表","利润表","现金流量表","盈利预测","分业务","主要财务数据","财务数据","关键比率","评级","目标价","估值"]
T1_KW = ["投资要点","核心观点","核心结论","投资建议","估值分析","风险提示","盈利拆分"]
T2_KW = ["行业分析","竞争格局","公司业务","经营情况","管理层讨论","业务展望","主营业务"]
T3_KW = ["免责声明","评级定义","分析师声明","重要声明","目录","附录","封面"]
```
顺序：`T3命中/首尾无实质 → T3` → `T0命中 或 (有表且含财务列) → T0` → `T1命中` → `T2命中或纯散文` → 兜底 `T2`。

### 1.3 新增 `src/extractors/table_serializer.py`

```python
Table = {
    "table_id": "t_04", "page_idx": 12, "caption": "合并利润表", "unit": "亿元",
    "headers": ["项目","2026H1","2025H1","YoY"],
    "rows": [["营业收入","204.42","195.14","4.75%"], ...],
    "html": "<table>...</table>",   # 原始 HTML 保留（前端渲染 / 溯源双视图）
}

def parse_table_html(html) -> List[List[str]]          # lxml，展开 rowspan/colspan
def serialize_table(table, rows_per_chunk=30) -> List[str]
```

**格式：Markdown 表格 + 分块重复表头**
```
[表 t_04 | 合并利润表 | 单位: 亿元 | 来源: p12]
| 项目 | 2026H1 | 2025H1 | YoY |
|---|---|---|---|
| 营业收入 | 204.42 | 195.14 | 4.75% |
```
超 30 行则按行边界分块、每块重复表头。
→ 与「逐行前置表头」同等的分块安全性，token 省约 50%（4 列 20 行：~320 vs ~600）。

### 1.4 新增 `src/extractors/fact_extractor.py`

```python
Fact = {
    "fact_id": "f_0031",
    "company": "复星医药", "metric": "营业收入", "period": "2026H1",
    "value": 204.42, "raw": "204.42", "unit": "亿元", "yoy": 4.75,
    "source": {"page_idx":12, "table_id":"t_04", "row":3, "col":"2026H1"},
    "confidence": 0.9,                     # 表格定位 0.9 / 文本正则 0.6 / LLM补全 0.5
    "dedup_key": "复星医药|营业收入|2026H1",
}

def extract_facts(sections, tables, companies) -> List[Fact]
```

**三层抽取**（规则优先，银行合规要求判定可审计）：
1. **表格定位（0.9）**：`metric`=行首列，`period`=列表头列，遍历行列交叉点
2. **文本正则（0.6）**：收敛 `entity_extractor.py:66-74` 与 `page_compressor.py:250-269` 两份重复正则为**单一副本**（顺带修体检问题 ⑬）
3. **LLM 别名归一（0.5，受限叶子，可关）**：仅归一「营收/营业收入/收入」，不参与核心判定

**去重**：`dedup_key` 相同保留 confidence 最高者，其余降为附加出处。

### 1.5 新增 `src/extractors/l1_builder.py` + 缓存位置

```python
def build_l1(structured_pages, companies) -> Dict:
    """{"sections": [...], "tables": [...], "facts": [...], "stats": {...}}"""
```

**缓存位置**：L1 是 `structured_pages` 的**确定性函数** → 在解析层构建，与解析结果一同落 `parse_cache`（复用 SHA-256 内容哈希）。命中缓存时一起返回，零重建。

### 1.6 新增 `tests/test_l1_pipeline.py`

| 用例 | 断言 |
|---|---|
| heading 归一化 | `title` / `header` / 仅 `text_level>0` 三种输入均识别为 heading |
| 切分 | 3 个 heading → 3 个 Section；无 heading → 1 个 Section 不崩 |
| 分档 | 「合并利润表」→T0、「免责声明」→T3、无关键词散文→T2，且 `tier_reason` 非空 |
| 表格 | rowspan/colspan 展开后行列对齐 |
| 表格 | 60 行宽表分块后**每块都带表头** |
| Fact | 表格来源绑定 company/metric/period，source 含 row/col |
| Fact | 同数字两次出现 → `dedup_key` 相同只留一条 |
| **回归** | 两页同名指标不再互相覆盖（dict 碰撞已根治） |

### 1.7 P1 验收
- [ ] 真实研报跑通 `build_l1`，产出 sections / tables / facts
- [ ] 表格任意切分点列语义可辨（抽检 5 个切分点）
- [ ] Fact 表格来源准确率抽检 ≥ 20 条
- [ ] **LLM 调用次数 == 0**（mock 断言）
- [ ] 缓存二次命中不重建 L1
- [ ] 现有 43 条测试仍全绿

---

## 二、P2 装配 + Agent 工具化

### 2.1 新增 `src/utils/token_counter.py`
```python
def count_tokens(text) -> int          # tiktoken cl100k_base
def count_tokens_safe(text) -> int     # × 1.15 安全余量
```

### 2.2 新增 `src/extractors/context_assembler.py`
```python
def assemble(user_query, budget_tokens, l1, l3=None) -> Dict:
    """{"context": str, "used": int, "injected": [...], "pointers": [...], "stats": {...}}"""
```
四步（严格实现 `compression-plan.md` §六）：
1. L3 便签必注入（若无 L3 则跳过，P3 才做）
2. L1 按 `T0 > T1 > T2` 贪心——**装得下就放无损原文，不压缩**；T3 跳过
3. 溢出：T0 只做 `compact_table`（紧凑化，不语义压缩）；T1/T2 暂**留指针**（P3 前降级手段只有指针 + 工具回取）
4. 剩余章节留 `fetch_context` 句柄

**硬约束**：装配结果 token **必须** ≤ `budget_tokens`，装配器末尾硬截断兜底并断言。

### 2.3 `llm_client.py` 增加工具调用支持
```python
def safe_invoke_with_tools(system_prompt, user_message, tools, max_rounds=3) -> Dict:
    """返回 {"error":False, "content":..., "tool_calls":[...], "rounds":N, "usage":{...}}"""
```
用 `llm.bind_tools(tools)` + 有界循环：调 LLM → 有 tool_calls 则执行并回填 → 再调，直到无 tool_calls 或达 `max_rounds`。

### 2.4 Agent 有界工具循环（ReAct-lite）

在 `financial_analyst.py`（先行）内实现：
```
1. assemble(budget) → 初始上下文
2. LLM(+tools) → 若返回 tool_calls：
     · 执行（确定性 Python 函数）
     · 每条记入 tool_call_history（谁/何时/工具/参数/返回体量/耗时）
     · 结果回填，再调 LLM
3. 循环至无 tool_calls 或 max_tool_calls 达上限
4. 返回最终文本
```

**两笔必付的账**（`system-review.md` §五）：
- **工具审计留痕**：每次调用写全 `tool_call_history`（谁、何时、工具、参数、返回体量、耗时）
- **工具预算**：新增配置 `MAX_TOOL_CALLS_PER_AGENT`（默认 5）+ 节点超时熔断

### 2.5 P2 交付的工具

| 工具 | 能力 | 来源 |
|---|---|---|
| `fetch_context(scope)` | 按需取章节 / 页范围原文 | L1 sections + tables |
| `query_fact(company, metric, period)` | 精确取数（带溯源） | L1 facts |
| `search_section(query, top_k)` | 关键词检索章节 | L1 sections |

> `verify_citation([P n])`（校验页码引用真实性，修体检问题 ⑧）留待 Reviewer 工具化时再做。

### 2.6 state 字段
新增：`pdf_l1`（结构化）/ `pdf_context`（装配产物，Agent 唯一消费入口）/ `tool_call_history`（已有，扩展字段）。
`pdf_sections` / `pdf_summary` **继续保留**（P4 未做，暂不删）。

### 2.7 P2 验收
- [ ] 20–40 页研报装配走「零压缩直注」，`LLM 压缩调用 == 0`
- [ ] 装配 token ≤ 预算（含 100+ 页长文档）
- [ ] Analyst 能自主调 `query_fact` 取到正确数值（端到端断言）
- [ ] 每次工具调用均有 `tool_call_history` 留痕
- [ ] 工具调用超上限时熔断，不失控
- [ ] 100+ 页长文档不再触发上下文溢出

---

## 三、P3 压缩 + P4 切换（本次范围，接续 P2）

### 3.1 P3：章节级 L2 / L3（年报刚需）

新建 `src/extractors/section_compressor.py`（取代 `page_compressor.py`，P4 删除旧文件）：

| 维度 | 旧（page_compressor） | 新（section_compressor） |
|---|---|---|
| 压缩单元 | 逐页 | **按 Section** |
| 触发 | 无条件，每页都压 | **仅装配判定的溢出章节** |
| 数字 | 压进自由文本 `financial_data` | **保留原值 + `[pNN]` 溯源**（不用 fact_id 替代，悬空指针论证见 `compression-plan.md` §5.3） |
| 表格 | `re.sub` 剥 HTML + `[:3000]` 截断 | 用 `table_serializer` 结构化输出，无截断 |

**设计要点**：
- **L2 缓存**：首次溢出构建，存 `pdf_l2`，跨 Agent / 跨修订轮复用（修订循环第二轮起压缩调用 == 0）
- **L3 全局摘要**：`{核心结论, 评级, 目标价, 关键财务亮点(company×metric×period), 风险}`，装配器第 1 步必注入
- **L2 压缩强度用真实年报标定**（全计划唯一「按需定」的参数）

**顺带修**：`_generate_summary` dict 碰撞（`page_compressor.py:332`）；删除逻辑不成立的 `_extract_financial_data_from_table`（`:232-247`）

**P3 验收**：
- [ ] L2 散文数字 100% 原值，无 `f_00xx` 悬空引用（正则抽检）
- [ ] 每个 L2 要点带 `[pNN]` 溯源
- [ ] 修订循环第二轮起 L2 压缩调用 == 0（缓存复用）
- [ ] 200 页年报装配后 token ≤ 预算

### 3.2 P4：切换 + 结束双轨付费

- `config.py`：`USE_MULTILEVEL_COMPRESSION`（P2 引入，此处默认值翻转为 `True`）
- `report_writer.py` 迁移到消费 `pdf_context`（Analyst 已在 P2 迁移）
- **删除**：`page_compressor.py`、state 中 `pdf_sections` / `pdf_summary`
- 回滚：开关置 `False` 即回旧路径（旧路径删除前）

**P4 验收**：
- [ ] 全部测试绿（43 条回归 + 新增）
- [ ] 同一份真实年报新旧对比：注入 token / LLM 压缩调用次数 / 数字可溯源率（抽检 10 个数）
- [ ] 旧路径删除后无 import 残留、无死代码

---

## 四、并行独立项（不阻塞本主线）

按 `system-review.md` §四 建议顺序：
1. **prompt caching**（半天，收益最大）
2. **重试退避 + 成本观测**（一天，运维地基）
3. **公司名 → 股票代码**（半天，补功能洞）
4. **端到端测试**（一天，安全网）
5. max_tokens 分项配置 / 真流式 / 其余打磨

---

## 五、文件清单与执行顺序

| 序 | 文件 | 动作 | 阶段 |
|---|---|---|---|
| 1 | `requirements.txt` | 加 `lxml` / `tiktoken` | P1 |
| 2 | `src/extractors/mineru_extractor.py` | heading 归一化 | P1 |
| 3 | `src/extractors/section_segmenter.py` | 新增 | P1 |
| 4 | `src/extractors/table_serializer.py` | 新增（lxml） | P1 |
| 5 | `src/extractors/fact_extractor.py` | 新增（收敛两份重复正则） | P1 |
| 6 | `src/extractors/l1_builder.py` | 新增 | P1 |
| 7 | `src/extractors/parse_cache.py` | L1 与解析结果一同缓存 | P1 |
| 8 | `tests/test_l1_pipeline.py` | 新增 | P1 |
| 9 | `src/utils/token_counter.py` | 新增（tiktoken） | P2 |
| 10 | `src/extractors/context_assembler.py` | 新增 | P2 |
| 11 | `src/utils/llm_client.py` | 加 `safe_invoke_with_tools` | P2 |
| 12 | `src/tools/financial_tools.py` | 新增 `fetch_context` / `query_fact` / `search_section` | P2 |
| 13 | `src/agents/financial_analyst.py` | 有界工具循环 + 消费 `pdf_context` | P2 |
| 14 | `src/graphs/state.py` | 加 `pdf_l1` / `pdf_l2` / `pdf_l3` / `pdf_context`；扩展 `tool_call_history` | P2 |
| 15 | `src/utils/config.py` | 加 `CONTEXT_BUDGET_TOKENS` / `MAX_TOOL_CALLS_PER_AGENT` / `USE_MULTILEVEL_COMPRESSION` | P2 |
| 16 | `src/extractors/section_compressor.py` | 新增（章节级 L2 + L3 reduce，取代 page_compressor） | P3 |
| 17 | `src/agents/report_extractor.py` | 接入新链路（L1 + 装配），旧压缩仅在开关关闭时走 | P3 |
| 18 | `src/agents/report_writer.py` | 迁移消费 `pdf_context` | P4 |
| 19 | `src/extractors/page_compressor.py` + state 旧字段 | **删除**（结束双轨付费） | P4 |
| 20 | `tests/` | 新增 P2/P3/P4 用例 + 43 条回归 | 全程 |

---

## 六、风险与开放决策点

| 风险 | 缓解 |
|---|---|
| MinerU heading 层级识别不准 | 归一化兼容三种写法；分档错**只影响装配优先级与压缩强度，不影响 L1 无损性** |
| tiktoken 与 DeepSeek 分词偏差 | 15% 安全余量 + 装配器硬上限兜底 |
| 表格序列化膨胀（宽表） | 小表（≤5 行）整表保留；宽表分块重复表头控制冗余 |
| Fact 抽取覆盖率不足 | 规则漏抽只降级为「无 fact 引用」，正文仍走 L1 原文，**不丢数据** |
| Agent 工具循环失控 | `MAX_TOOL_CALLS_PER_AGENT` + 节点超时双保险 |
| P2→P4 双成本窗口 | 窗口内旧链路仍运行；按阶段尽快推进 P3/P4 关窗 |
| L2 压缩强度拍脑袋 | 用真实年报实测标定（全计划唯一「按需定」参数） |

**开放决策点（需你拍板）**：
1. `CONTEXT_BUDGET_TOKENS` 默认 **24000** —— 是否按 Agent 分别配？建议先用统一值，实测后再分。
2. `MAX_TOOL_CALLS_PER_AGENT` 默认 **5** —— 够不够 Analyst 用？建议先 5，实测调。
3. **真实研报样本**：`data/` 下有待确认；无样本只能用 fixture，P1 验收说服力打折。
