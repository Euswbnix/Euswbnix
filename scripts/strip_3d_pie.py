#!/usr/bin/env python3
"""Remove the language pie from github-profile-3d-contrib output.

That pie only sees public repositories and can't be switched off in the action's settings
(full mode always draws calendar + radar + pie). Our own language card replaces it.

The pie is a top-level <g transform="translate(40, …)"> whose slices carry <title>Lang N</title>.
Edits a file only when exactly one such group is found; otherwise leaves it untouched and exits 1.
"""
import re, sys, xml.etree.ElementTree as ET

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
SLICE_TITLE = re.compile(r"^\S.* \d+$")          # "Python 250" — radar titles are bare numbers


def is_pie(g):
    if not g.get("transform", "").replace(" ", "").startswith("translate(40,"):
        return False
    titles = [t.text or "" for t in g.iter(f"{{{NS}}}title")]
    return any(SLICE_TITLE.match(t) for t in titles)


status = 0
for path in sys.argv[1:]:
    tree = ET.parse(path)
    root = tree.getroot()
    hits = [g for g in root.findall(f"{{{NS}}}g") if is_pie(g)]
    if len(hits) != 1:
        print(f"{path}: expected 1 pie group, found {len(hits)} — left unchanged")
        status = 1
        continue
    root.remove(hits[0])
    tree.write(path, encoding="utf-8", xml_declaration=False)
    print(f"{path}: pie removed")
sys.exit(status)
