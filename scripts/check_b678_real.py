import sys, json
sys.path.insert(0, r'd:\develop\财报分析系统\src')
from pathlib import Path
from collections import Counter

# 用法: python check_b678_real.py <content_list.json 路径> <公司名>
DEFAULT = r'D:\develop\财报分析助手\m1\out\joinn_v3\joinn_2024_annual\auto\joinn_2024_annual_content_list.json'
cl_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
company = sys.argv[2] if len(sys.argv) > 2 else '昭衍新药'

cl = json.loads(Path(cl_path).read_text(encoding='utf-8'))
from extractors.mineru_extractor import _content_list_to_structured_pages
from extractors.l1_builder import build_l1
pages = _content_list_to_structured_pages(cl)
l1 = build_l1(pages, companies=[company])

# B8: 单位覆盖
tables = l1['tables']
with_unit = [t for t in tables if t['unit']]
print(f"B8: 单位覆盖 {len(with_unit)}/{len(tables)} 表格")
print('    单位分布:', dict(Counter(t['unit'] for t in with_unit)))

# B6: 别名命中
facts = l1['facts']
aliased = [f for f in facts if f.get('metric_std') != f['metric']]
print(f"B6: 别名命中 {len(aliased)}/{len(facts)} facts")
print('    样例:', [(f['metric'], '->', f['metric_std']) for f in aliased[:8]])

# B7: 勾稽
checks = l1['stats']['identity_checks']
ok = [c for c in checks if c.get('ok') is True]
bad = [c for c in checks if c.get('ok') is False]
mixed = [c for c in checks if c.get('ok') is None]
print(f"B7: 勾稽 {len(ok)} 通过 / {len(bad)} 不符 / {len(mixed)} 单位口径不明")
for c in ok[:4]:
    print(f"    PASS {c['identity']} @ {c['period']}: {c['actual']:.4g} vs {c['expected']:.4g}")
for c in bad[:4]:
    print(f"    FAIL {c['identity']} @ {c['period']}: 实际 {c['actual']:.6g} vs 期望 {c['expected']:.6g}")
for c in mixed[:2]:
    print(f"    SKIP {c['identity']} @ {c['period']} ({c['reason']})")
