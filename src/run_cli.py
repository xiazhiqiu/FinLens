#!/usr/bin/env python3
"""
FinScope 命令行调试入口

无需启动 Streamlit，直接从命令行测试核心链路。

使用方式:
    python src/run_cli.py "分析复星医药的财务表现"
    python src/run_cli.py --code 600196 "分析这只股票"
    python src/run_cli.py --code 600196 --pdf ./data/sample.pdf "分析"
"""

import sys
import os
import argparse
import time
from datetime import datetime

# Windows GBK 编码兼容
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 确保 src/ 在路径中
sys.path.insert(0, os.path.dirname(__file__))

from graphs.financial_graph import FinancialAnalysisGraph
from graphs.state import create_initial_state
from utils.config import get_settings
from utils.llm_client import is_llm_ready


def print_separator(title: str = ""):
    if title:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")
    else:
        print(f"{'='*60}")


def print_state_summary(state: dict):
    print(f"  迭代次数: {state.get('iteration_count', '?')}")
    print(f"  下一Agent: {state.get('next_agent', '?')}")
    print(f"  实体数量: {len(state.get('extracted_entities', []))}")
    print(f"  财务数据: {'有' if state.get('financial_data') else '无'}")
    print(f"  分析结果: {len(state.get('analysis_result', ''))} 字符")
    print(f"  最终报告: {len(state.get('final_report', ''))} 字符")
    errors = state.get("error_log", [])
    if errors:
        print(f"  错误日志 ({len(errors)}条):")
        for e in errors[-3:]:
            print(f"     - {e[:100]}")


def main():
    parser = argparse.ArgumentParser(
        description="FinScope CLI 调试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_cli.py "分析复星医药的财务表现"
  python run_cli.py --code 600196 "分析这只股票"
  python run_cli.py --code 000001 --type macro "分析银行股"
        """,
    )
    parser.add_argument("query", nargs="?", default="分析A股市场", help="金融分析查询")
    parser.add_argument("--code", "-c", default="", help="股票代码")
    parser.add_argument("--type", "-t", default="company", choices=["company", "industry", "macro", "strategy"], help="分析类型")
    parser.add_argument("--pdf", "-p", default="", help="研报PDF路径")
    parser.add_argument("--stream", "-s", action="store_true", help="流式模式")
    parser.add_argument("--thread", default="cli-debug", help="会话 thread_id")

    args = parser.parse_args()

    print_separator("FinScope CLI 调试工具")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    settings = get_settings()
    api_ready = is_llm_ready()
    print(f"LLM: {'[READY] 已配置' if api_ready else '[WARN] 未配置 (规则回退模式)'}")
    print(f"Provider: {settings.LLM_PROVIDER}")
    print(f"SQLite: {settings.SQLITE_PATH}")

    query = args.query
    if args.code:
        query = f"{query}\n[股票代码: {args.code}]"

    print(f"\n查询: {query}")
    print(f"类型: {args.type}")
    if args.pdf:
        if os.path.isfile(args.pdf):
            print(f"PDF: {args.pdf} ({os.path.getsize(args.pdf)/1024:.0f} KB)")
        else:
            print(f"PDF 不存在: {args.pdf}")
            args.pdf = ""

    print_separator("初始化 FinancialAnalysisGraph")
    graph = FinancialAnalysisGraph(sqlite_path=settings.SQLITE_PATH)
    compiled = graph.compile()
    print(f"图编译完成: {type(compiled).__name__}")

    print_separator("执行分析")
    start_time = time.time()

    if args.stream:
        node_index = 0
        last_state = {}
        for chunk in graph.stream(
            user_query=query,
            report_type=args.type,
            pdf_path=args.pdf,
            thread_id=args.thread,
        ):
            node_index += 1
            for node_name, node_output in chunk.items():
                print(f"\n--- Step {node_index}: [{node_name}] ---")
                print_state_summary(node_output)
                if node_name == "report_writer":
                    last_state = node_output

        state = last_state
        print(f"\n流式执行完成，共 {node_index} 步")
    else:
        state = graph.invoke(
            user_query=query,
            report_type=args.type,
            pdf_path=args.pdf,
            thread_id=args.thread,
        )
        print_state_summary(state)

    total_time = time.time() - start_time

    print_separator("最终分析报告")
    final_report = state.get("final_report", "") if state else ""

    if final_report:
        print(final_report)
    else:
        print("(未生成最终报告)")

    print_separator()
    print(f"总耗时: {total_time:.2f}秒")

    errors = state.get("error_log", []) if not args.stream else []
    if errors:
        print(f"\n执行过程中出现 {len(errors)} 个错误:")
        for i, e in enumerate(errors, 1):
            print(f"  {i}. {e}")


if __name__ == "__main__":
    main()
