# FinScope 财报上下文压缩方案（多级 / 分类型 / 预算驱动）

> 目标：替换当前 `page_compressor.py` 的「逐页无差别 LLM 压缩」。
> 原则：**能不压就不压，非压不可时按内容类型定损，压也要压得结构化。**
>
> 落地步骤见 **`docs/implementation-plan.md`**（文件清单 / 数据结构 / 测试 / 验收标准）。

---

## 一、当前方案的五个错（回顾）

| # | 问题 | 代码证据 |
|---|---|---|
| 1 | **Eager 全压**：不管上下文放不放得下，每页都灌一次 LLM | `page_compressor.py:58/67` 循环逐页调 LLM；`report_extractor.py:118` |
| 2 | **逐页孤立**：跨页表格、跨页同一公司的论述被切断 | `page_compressor.py:67` 单页独立压缩，无跨页上下文 |
| 3 | **数字进自由文本**：财务数值压成 `financial_data{"指标名":"数值"}`，无公司/期间绑定 | `:152` JSON schema；`:39` 扁平 dict |
| 4 | **dict 碰撞丢数据**：`_generate_summary` 用 `dict.update()` 合并，同名 key 后页覆盖前页 | `:332` |
| 5 | **表格在压缩入口就被剥标签 + 页内截断**：`_items_to_text` 正则去 HTML，且 `[:3000]` 砍尾部 | `:210`、`:139` |

根因：这套是上一轮「上下文溢出」的兜底 band-aid，用「先全压成 key_points」换取窗口可控，代价是**精度、结构、成本**三输。

---

## 二、三条设计原则

1. **分类型定损（Heterogeneous loss tolerance）**
   财报内容的容错度天生不同：财务数字零容错，论述过程可丢细节，免责声明该直接丢。**不能无差别压缩。**

2. **多级降级（Graceful degradation），而非一刀切**
   保住无损层，按预算逐级降级；预算够就完全不压。

3. **压缩是「放不下」的应对，不是默认动作**
   压缩由 token 预算触发，且优先用「按需回取」替代「预压缩丢弃」。

---

## 三、架构：L0–L3 四级 + 分发层

```
L0 原始层   structured_pages（MinerU 全量，含 HTML 表格）
            │  落 parse_cache / 文件；用句柄引用；★ 永不进 prompt
            ▼  版面清洗 + 章节切分 + 表格序列化 + 事实抽取
L1 结构化无损层   sections[]（章节化文本）+ facts[]（事实记录）+ tables[]（逐行前置表头）
            │  ★ 数字全保留，token 量约为原文 40–60%（去掉版面噪声/样板/重复表头）
            ▼  仅对「预算溢出」或「低优先级」章节做语义压缩
L2 章节语义层   per-section { thesis, key_arguments }
            │  ★ 只压散文、数字内联保留原值 + [pNN] 溯源标注
            │    不用 fact_id 替代散文中的数字（详见 §5.3 悬空指针论证）
            ▼  跨章节 reduce
L3 全局摘要层   { 核心结论, 评级, 目标价, 关键财务亮点(company×metric×period), 风险 }
            │
            ▼
分发层 Context Assembler（预算驱动装配）+ 检索句柄 fetch_context()
```

**关键差异 vs 现方案**：当前是「L0 → 直接跳到自由文本」；新方案是「L0 → L1 无损结构化」作为主力，L2/L3 仅在预算不足时才启用。

---

## 四、分类型定损：T0–T3 优先级

章节切分后，按标题关键词 + 内容构成判定类型（规则为主，确定性、可审计，契合项目「规则计算层」定位）：

| 档 | 内容 | 压缩策略 | 溢出处理 |
|---|---|---|---|
| **T0** | 三大报表、盈利预测表、分业务数据、评级/目标价、关键比率 | **永不压缩**，走 L1 事实抽取 + 表格序列化 | 永不降层 |
| **T1** | 投资要点、核心结论、估值分析、风险提示 | 轻压缩（保留准原句 **+ 数字原值**） | 溢出才降 L2 |
| **T2** | 行业分析、竞争格局、公司业务描述、管理层讨论 | 可重压缩（bullets，丢细节可接受） | **优先降 L2** |
| **T3** | 免责声明、评级定义、分析师声明、目录、封面、附录 | **直接丢弃** | — |

章节切分依据：`structured_pages` 中 `item["type"] == "header"` + `item["level"]`（level 1 = 章，level 2 = 节）与 `page_idx` 边界。

> **⚠️ 已修正**：初版写作「MinerU `content_list` 的 `type=title`」，与本项目实现不符。
> 本项目 `mineru_extractor.py:302-310` 已将 MinerU 原始 `type=title` **归一化为 `type="header"`**，
> 层级字段取自 `text_level`。切分器必须按 `header` 匹配，否则一章都切不出来。

---

## 五、L1 两个核心数据结构（解决精度与结构）

### 5.1 表格序列化：逐行前置表头（自然分页）

```
[表 t_04 | 单位: 亿元 | 来源: p12]
项目 | 2026H1 | 2025H1 | YoY
项目=营业收入 | 2026H1=204.42 | 2025H1=195.14 | YoY=4.75%
项目=归母净利润 | 2026H1=17.21 | 2025H1=15.87 | YoY=8.44%
```

每行自带完整列语义 → 切分/分页边界对齐行边界即**无损**（此前调研结论：RAGFlow / Docling `repeat_table_header` 同思路）。
原始 HTML 另行保留，供前端渲染与溯源（**双视图**）。

### 5.2 事实记录 Fact（根治 dict 碰撞与无主体绑定）

```python
Fact = {
    "fact_id": "f_0031",
    "company": "复星医药",        # 主体绑定（当前缺失）
    "metric":  "营业收入",         # 标准化科目名
    "period":  "2026H1",          # 报告期绑定（当前缺失）
    "value":   204.42, "unit": "亿元",
    "yoy":     4.75,
    "source":  {"page_idx": 12, "table_id": "t_04", "row": 3, "col": "2026H1"},  # 溯源（当前缺失）
    "confidence": 0.9,
}
```

抽取方式：**规则为主**（复用 `entity_extractor` 的 7 类指标 + 表格行列定位），LLM 仅做别名归一与低置信补全。

### 5.3 设计更正：L2 不用 fact_id 替代数字

**原设计缺陷**：初版设想 L2 压缩时「数字不复述，只留 `fact_id` 引用」。此设计不成立——L2 的启用前提正是 L1 全文装不下，即被引用的 Fact **大概率不在上下文中**，此时 `f_0031` 对 LLM 是一个无意义的**悬空指针**：值丢了、token 照占、还可能诱导模型去猜一个数。比直接写数字更糟。

**正确的压缩对象是散文，不是数字**。研报的 token 大头是叙述性文字：

| 内容（单页量级） | 原始 | 压后 | 节省 | 代价 |
|---|---|---|---|---|
| 叙述散文 ~800 字 | ~800 token | ~80 token | **720** | 丢细节，可接受 |
| 财务表 15 行（已序列化） | ~300 token | ~60 token | 240 | **丢全部数字，不可接受** |

压缩散文的收益约为压表格的 3 倍，代价却接近零。而单个数字仅 4–6 token（"204.42"），一句话带 3 个数也不过 20 token——**数字很便宜，省它不划算**。

> 注：上表为估算量级，落地 P1 阶段须用真实研报标定。

**fact_id 的正确用途**（三个，均非「在散文中替代数字」）：

1. **溯源**——每个数字可回溯到 `page/table/row/col`，满足银行审计与可解释性要求；
2. **去重**——同一数字在全文多处出现只存一份；
3. **构建紧凑事实表**——把散落全文的数字聚成 `company × metric × period` 表，这本身就是高性价比压缩：T0 大表（如 60 行）序列化后约 1.2k token，远优于压成散文。

**修正后 L2 形态**：

```
[财务表现 · p12-14]
· 营收 204.42 亿(+4.75%)，归母净利 17.21 亿(+8.44%)，
  利润增速快于收入，结构改善 [p12]
· 毛利率 49.67%，同比 +1.47pct，主因创新药占比提升 [p13]
· 明细可调 fetch_context(t_04)
```

数字原值在内（可继续推理）、`[pNN]` 提供溯源、`fetch_context` 作为按需深挖通道。

---

## 六、预算驱动装配算法（核心：放不下再压）

```python
def assemble(user_query, budget_tokens, L1, L2, L3) -> str:
    ctx, used = [], 0

    # 1) L3 全局摘要：体量小，始终注入
    ctx.append(L3); used += count(L3)

    # 2) L1 按优先级降序（T0 > T1 > T2；T3 跳过）贪心塞入
    for section in sort_by_priority(L1, query=user_query):
        size = count(section)
        if used + size <= budget:
            ctx.append(section); used += size          # ★ 塞得下就放无损原文，不压缩
        elif section.tier == "T0":
            ctx.append(compact_table(section))          # T0 只做表格紧凑化，不语义压缩
            used += count(compact_table(section))
        else:
            # 3) 放不下 → 降级到该章节的 L2 压缩版
            ctx.append(L2[section.id]); used += count(L2[section.id])

    # 4) 仍未注入的章节 → 只留一行指针 + 检索句柄
    ctx.append(pointer_to_remaining(...))
    return render(ctx)
```

**触发压缩的唯一条件**：某章节 L1 原文超出剩余预算。
→ 20–40 页研报常见 L1 在 10–20k token，**大概率直接命中「不压缩」路径**，省掉全部 LLM 压缩成本（当前是 100% 全压）。

### 检索句柄（根治「丢弃」，而非「压缩」）

给 Analyst/Writer 挂载工具：

```
fetch_context(scope)  # scope: section_id | fact_query(company+metric+period) | page_range
```

放不下的内容**不是被压掉，而是可按需回取**——这才符合多智能体「隔离 + 按需选择」范式，也是 LangGraph 官方「大结果卸载文件系统」模式。

---

## 七、模块与文件清单

| 文件 | 职责 | 状态 |
|---|---|---|
| `src/extractors/table_serializer.py` | HTML → 结构化 Table（展开 rowspan/colspan）+ 逐行前置表头序列化 | **新增**（此前调研已确定必须做） |
| `src/extractors/section_segmenter.py` | 按 MinerU heading 切逻辑章节 + 判定 T0–T3 类型 | **新增** |
| `src/extractors/fact_extractor.py` | 表格/文本 → Fact 记录（规则为主 + LLM 别名归一） | **新增** |
| `src/extractors/context_assembler.py` | 预算驱动装配器 + 检索句柄 + token 计数 | **新增**（供 `fetch_context` 工具复用） |
| `src/extractors/page_compressor.py` | 改造：逐页 → **按章节**压缩（L2），数字改留 fact_id 引用，去掉 `[:3000]` 截断 | **重构** |
| `src/agents/report_extractor.py` | 产出 L1/L2/L3 三层；不再无条件 `compress_pages` | **改造** |
| `src/graphs/state.py` | 新增 `pdf_l1` / `pdf_l2`；`pdf_sections` 保留作向后兼容 | **改造** |
| `src/utils/config.py` | `USE_MULTILEVEL_COMPRESSION`（灰度开关）、`CONTEXT_BUDGET_TOKENS` | **改造** |
| `requirements.txt` | 新增 `tiktoken`（token 计数；中文需乘标定系数 + 安全余量） | **改造** |

---

## 八、落地步骤（灰度 + 可回滚）

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P1 地基** | `table_serializer` + `section_segmenter` + `fact_extractor`，产出 L1（**不做任何 LLM 压缩**） | L1 事实抽取在真实研报上准确率抽检；表格跨 chunk 不丢表头 |
| **P2 装配** | `context_assembler` 预算驱动装配 + `fetch_context` 工具挂载 | 20–40 页研报走「零压缩直注」路径；长文档按预算降级不溢出 |
| **P3 压缩** | `page_compressor` 改造为章节级 L2 + L3 reduce | L2 数字 100% 引用 fact_id，无自由文本复述 |
| **P4 切换** | 灰度开关默认打开，旧路径保留一个版本后移除 | 43+ 测试全绿；对比旧方案 token 成本与答案质量 |

---

## 九、验证方式

- **单元测试**：表格序列化跨切分无损、Fact 抽取主体/期间绑定、`_generate_summary` 不再 dict 碰撞、装配器预算不越界。
- **回归**：现有 43 条测试全绿（含 revise 循环、血缘、合规门）。
- **实测**：取一份真实长研报（100+ 页），对比新/旧方案的：注入 token 数、LLM 压缩调用次数、最终报告的数值准确率（人工抽检 10 个数字是否可在原文溯源）。

---

## 十、风险与权衡

| 风险 | 说明 | 缓解 |
|---|---|---|
| **章节切分质量** | 依赖 MinerU heading 层级；标题识别错则 T0–T3 分档错 | 规则 + LLM 二次校正；分档错只影响压缩强度，不影响 L1 无损性 |
| **token 计数误差** | tiktoken cl100k 对中文与 DeepSeek 分词有偏差 | 中文标定系数 + 15% 安全余量；装配器硬上限兜底 |
| **Fact 抽取覆盖率** | 规则抽取可能漏非标科目 | LLM 补全低置信项；漏抽只降级为「无 fact 引用」，正文仍走 L1 原文 |
| **改造面** | 涉及 7 个文件 | 灰度开关 + 分阶段，每阶段独立可验收 |
| **L1 未必更小** | 表格序列化会膨胀 token（宽表 2–4×） | 小表（≤5 行）整表保留；宽表按行分组重复表头控制冗余；膨胀的是「有价值的数字」，比丢精度划算 |
