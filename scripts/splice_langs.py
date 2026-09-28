#!/usr/bin/env python3
"""Copy the <!--LANGS:START--> ... <!--LANGS:END--> block from SRC into DST.

Everything outside the block is taken from DST, so the freshly generated
language numbers can be committed on top of the latest main even when this
run checked out an older commit (a manual "Re-run" checks out the original SHA).

usage: splice_langs.py SRC DST
"""
import re
import sys

BLOCK = re.compile(r"<!--LANGS:START-->.*?<!--LANGS:END-->", re.S)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        block = BLOCK.search(f.read())
    with open(dst, encoding="utf-8") as f:
        text = f.read()
    if not block or not BLOCK.search(text):
        sys.exit("README markers <!--LANGS:START--> / <!--LANGS:END--> not found")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(BLOCK.sub(lambda _: block.group(0), text, count=1))


if __name__ == "__main__":
    main()
