#!/usr/bin/env python3
"""Extract the reference DDL (```sql blocks in section 3.1-3.8) from the schema doc.

Usage: python scripts/extract_ddl.py [path/to/CRICKET_DATABASE_SCHEMA.md] > reference.sql

Section 3.9 (example queries) is deliberately excluded: those statements use
bind parameters and are not DDL. Fails loudly if the doc structure changes.
"""
import re
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/CRICKET_DATABASE_SCHEMA.md")
doc = path.read_text(encoding="utf-8")

start = re.search(r"^## 3\. ", doc, re.M)
end = re.search(r"^### 3\.9 ", doc, re.M)
if not start or not end or end.start() <= start.start():
    sys.exit(f"{path}: could not locate the '## 3.' .. '### 3.9' boundaries")

blocks = re.findall(r"```sql\n(.*?)```", doc[start.start():end.start()], re.S)
if not blocks:
    sys.exit(f"{path}: no ```sql blocks found between section 3 and 3.9")

print("\n\n".join(b.strip() for b in blocks))
