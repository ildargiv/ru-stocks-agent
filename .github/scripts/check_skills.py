"""Every SKILL.md must start with a YAML frontmatter that has name, description and version."""
import pathlib
import re
import sys

bad = []
files = sorted(pathlib.Path("skills").rglob("SKILL.md"))
if not files:
    bad.append("no SKILL.md found under skills/")
for p in files:
    text = p.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        bad.append(f"{p}: no frontmatter")
        continue
    for key in ("name", "description", "version"):
        if not re.search(rf"^{key}:\s*\S", m.group(1), re.M):
            bad.append(f"{p}: missing '{key}'")
for b in bad:
    print("FAIL", b)
print(f"{len(files)} SKILL.md checked")
sys.exit(1 if bad else 0)
