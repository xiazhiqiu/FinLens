# FinScope

基于 LangGraph 多智能体协同的金融研报智能分析系统。

## 功能特性

- **MinerU 深度 PDF 解析**：精准表格识别 + 科目匹配 + 金融实体抽取
- **双数据源金融工具**：Tushare 深度数据 + AkShare 实时行情，自动降级
- **Supervisor 多智能体架构**：4 个专业 Agent 协同，LLM 动态调度
- **三层死循环防护**：硬熔断 + 单 Agent 限频 + 规则回退
- **多模型可配置**：DeepSeek / OpenAI / Ollama 无缝切换
- **流式可视化前端**：Streamlit 深蓝科技风 UI + 实时执行时间线

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM API Key 和 Tushare Token

# 3. 启动 Web 界面
streamlit run frontend/app.py

# 或使用 CLI 调试
python src/run_cli.py "分析复星医药的财务表现"
```

## 项目结构

```
FinScope/
├── src/
│   ├── agents/          # 5 个金融 Agent
│   ├── graphs/          # LangGraph 状态图
│   ├── tools/           # 金融工具链
│   ├── extractors/      # MinerU 解析层
│   └── utils/           # 配置 + LLM 客户端
├── frontend/            # Streamlit 界面
├── config/              # 财务科目映射
├── tests/               # 测试
└── data/                # 数据目录
```

## 技术栈

- **Agent 编排**：LangGraph StateGraph + Supervisor 条件路由
- **PDF 解析**：MinerU (PDF-Extract-Kit + VLM)
- **金融数据**：Tushare Pro + AkShare
- **大模型**：DeepSeek / OpenAI / Ollama
- **前端**：Streamlit
- **持久化**：SQLite (LangGraph Checkpoint)

## License

MIT License
