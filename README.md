# FinScope 金融研报智能分析系统

基于 LangGraph 多智能体协同的金融研报智能分析系统，集成 MinerU PDF 深度解析、多模型 LLM、双数据源金融工具链。

## 功能特性

### 核心能力

- **MinerU 深度 PDF 解析**：精准表格识别 + 科目匹配 + 金融实体抽取
- **PDF 深度利用**：LLM 逐页压缩提取关键信息 + 规则兜底，结构化引用 `[P 页码]`
- **双数据源金融工具**：Tushare 深度数据 + AkShare 实时行情，自动降级
- **Supervisor 多智能体架构**：5 个专业 Agent 协同，LLM 动态调度
- **Reviewer 复核机制**：LLM 质量审查 + 最多 2 次修订，电路保护
- **程序化来源表**：非 LLM 生成，保证数据来源可靠性

### 企业级特性

- **Security**：JWT 认证、RBAC 权限、AES-256 加密、输入防护
- **Compliance**：监管规则引擎、内容过滤、信息隔离墙
- **Audit**：不可变审计日志、数据血缘追踪
- **配置模板**：开发/测试/生产环境配置

### 工程特性

- **三层死循环防护**：硬熔断 + 单 Agent 限频 + 规则回退
- **多模型可配置**：DeepSeek / OpenAI / Ollama 无缝切换
- **流式可视化前端**：Streamlit 深蓝科技风 UI + 实时执行时间线
- **数据源状态监控**：前端实时显示 Tushare/AkShare/LLM 连接状态

## 快速开始

### 环境要求

- Python 3.11+
- MinerU（可选，用于 PDF 深度解析）
- Tushare Token（可选，用于金融数据）
- LLM API Key（必需，DeepSeek/OpenAI/Ollama 任选）

### 安装

```bash
# 1. 克隆项目
git clone https://github.com/xiazhiqiu/FinLens.git
cd FinLens

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM API Key 和 Tushare Token
```

### 环境变量配置

```env
# LLM 配置（必需）
LLM_PROVIDER=deepseek          # deepseek / openai / ollama
DEEPSEEK_API_KEY=your_key      # DeepSeek API Key
DEEPSEEK_MODEL=deepseek-chat   # 模型名称

# 金融数据（可选）
TUSHARE_TOKEN=your_token       # Tushare Pro Token
TUSHARE_PRIORITY=true          # 优先使用 Tushare

# 企业级（可选）
JWT_SECRET=your_secret         # JWT 密钥
```

### 启动

```bash
# Web 界面
streamlit run frontend/app.py

# CLI 调试
python src/run_cli.py "分析复星医药的财务表现"

# 带 PDF 分析
python src/run_cli.py "分析这份研报" --pdf path/to/report.pdf
```

## 项目结构

```
FinScope/
├── src/
│   ├── agents/                    # 5 个金融 Agent
│   │   ├── supervisor.py          # 调度员（LLM 路由）
│   │   ├── report_extractor.py    # PDF 抽取 + 页面压缩
│   │   ├── data_retriever.py      # 金融数据检索
│   │   ├── financial_analyst.py   # 深度分析（含引用规范）
│   │   └── report_writer.py       # 报告撰写 + 来源表
│   ├── graphs/
│   │   ├── state.py               # 共享状态定义
│   │   └── financial_graph.py     # LangGraph 拓扑
│   ├── tools/
│   │   └── financial_tools.py     # Tushare/AkShare 工具链
│   ├── extractors/
│   │   ├── mineru_extractor.py    # MinerU PDF 解析
│   │   ├── entity_extractor.py    # 正则实体抽取
│   │   └── page_compressor.py     # LLM 页面压缩 + 规则兜底
│   └── utils/
│       ├── config.py              # 配置管理
│       └── llm_client.py          # 多模型 LLM 客户端
├── enterprise/                    # 企业级模块
│   ├── security/                  # 认证/权限/加密
│   ├── compliance/                # 合规/内容过滤
│   └── audit/                     # 审计/血缘追踪
├── frontend/
│   └── app.py                     # Streamlit 界面
├── config/
│   ├── development.json           # 开发环境配置
│   ├── testing.json               # 测试环境配置
│   └── production.json            # 生产环境配置
├── tests/
│   ├── test_basic.py              # 基础测试（10 个）
│   └── test_enterprise.py         # 企业级测试（12 个）
├── Dockerfile                     # 多阶段 Docker 构建
├── docker-compose.yml             # 容器编排
└── requirements.txt               # Python 依赖
```

## 架构设计

### 多智能体协作流程

```
用户查询
    ↓
[Supervisor] ─── LLM 路由 ──→ [ReportExtractor] ──→ PDF 解析 + 实体抽取
    │                              ↓
    │                         页面压缩（LLM/规则）
    │                              ↓
    ├──→ [DataRetriever] ──→ Tushare/AkShare 数据检索
    │                              ↓
    ├──→ [FinancialAnalyst] ←── 整合数据 + 引用规范
    │                              ↓
    │                         LLM 深度分析（带 [P页码]）
    │                              ↓
    └──→ [ReportWriter] ───→ Markdown 报告 + 来源表
                                    ↓
                              [Reviewer] ──→ 质量审查
                                    ↓
                              通过 / 修订（最多 2 次）
```

### PDF 深度利用管线

```
PDF 文件
    ↓
MinerU 解析 → content_list.json（含 page_idx/type/bbox）
    ↓
结构化页面列表 [{page_idx, items: [{type, content, bbox}]}]
    ↓
LLM 逐页压缩 → key_points + financial_data + tables
    ↓                          ↓（LLM 不可用时）
    │                    规则压缩（正则提取）
    ↓
pdf_sections 写入 State
    ↓
FinancialAnalyst 注入压缩内容 + [P页码] 引用指令
    ↓
ReportWriter 撰告 + 程序化来源表
```

### 状态字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `messages` | List[BaseMessage] | 消息流（add_messages reducer） |
| `user_query` | str | 用户原始查询 |
| `report_type` | str | 研报类型（company/industry/macro/strategy） |
| `extracted_entities` | List[Dict] | 抽取的金融实体 |
| `financial_data` | Dict | 公开市场数据 |
| `pdf_sections` | List[Dict] | 压缩后的页面列表 |
| `pdf_summary` | str | 全文摘要 |
| `analysis_result` | str | 深度分析结论 |
| `final_report` | str | 最终报告 |
| `review_result` | str | 审查结果（pass/revise） |

## 技术栈

| 组件 | 技术 |
|------|------|
| Agent 编排 | LangGraph StateGraph + Supervisor 条件路由 |
| PDF 解析 | MinerU (PDF-Extract-Kit + VLM) |
| 页面压缩 | LLM 逐页压缩 + 规则正则兜底 |
| 金融数据 | Tushare Pro + AkShare |
| 大模型 | DeepSeek / OpenAI / Ollama |
| 前端 | Streamlit |
| 持久化 | SQLite (LangGraph Checkpoint) |
| 容器化 | Docker + Docker Compose |
| 企业级 | JWT + RBAC + AES-256 + 审计日缘 |

## 测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 仅基础测试
python -m pytest tests/test_basic.py -v

# 仅企业级测试
python -m pytest tests/test_enterprise.py -v
```

## Docker 部署

```bash
# 构建并启动
docker-compose up -d

# 服务列表
# - finscope: Web 应用（端口 8501）
# - redis: 缓存（端口 6379）
# - nginx: 反向代理（端口 80）
```

## 配置说明

### LLM 配置

```json
{
  "LLM_PROVIDER": "deepseek",
  "DEEPSEEK_API_KEY": "sk-xxx",
  "DEEPSEEK_MODEL": "deepseek-chat",
  "LLM_TEMPERATURE": 0.3,
  "LLM_MAX_TOKENS": 4096
}
```

### 企业级配置

```json
{
  "ENTERPRISE_MODE": true,
  "JWT_SECRET": "your-secret-key",
  "ENCRYPTION_KEY": "your-encryption-key",
  "AUDIT_LOG_ENABLED": true
}
```

## License

MIT License
