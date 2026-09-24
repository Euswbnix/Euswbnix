#!/usr/bin/env python3
"""Generate an animated language-share card for the GitHub profile README.

Counts code bytes (GitHub linguist) across every repository the token can see,
public and private, forks excluded. Writes:
  dist/lang-donut[-dark].svg      donut; auto-cycles a spotlight through languages
  dist/lang-row-NN[-dark].svg     one bar per language (hover tooltip lives in README)
  dist/lang-stats.json            the numbers behind the images
and rewrites the README block between <!--LANGS:START--> and <!--LANGS:END-->.

Images in a README are rendered through <img>, which receives no pointer events,
so hover is done with `title` on the link wrapping each row image.

Env: LANG_STATS_TOKEN (or GH_TOKEN) — needs read access to repository metadata.
"""
import hashlib, html, json, math, os, pathlib, sys, urllib.request

TOP_N = 10
STYLE = "1"  # bump when the SVG design changes, so GitHub's image cache refreshes
EXCLUDE = {"DouYinSparkFlow-Auto"} | {x for x in os.environ.get("LANG_EXCLUDE", "").split(",") if x}
RAW = "https://raw.githubusercontent.com/{owner}/{owner}/output/{name}"
OTHER_COLOR = "#8b949e"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"
THEMES = {
    "":      {"fg": "#1f2328", "muted": "#59636e", "track": "#eaeef2", "dim": 0.35},
    "-dark": {"fg": "#e6edf3", "muted": "#9198a1", "track": "#262c36", "dim": 0.45},
}
LINKS = {
    "Java": "https://dev.java", "Python": "https://www.python.org",
    "JavaScript": "https://developer.mozilla.org/docs/Web/JavaScript", "Vue": "https://vuejs.org",
    "Shell": "https://www.gnu.org/software/bash/", "TeX": "https://www.latex-project.org",
    "C#": "https://learn.microsoft.com/dotnet/csharp/", "HTML": "https://developer.mozilla.org/docs/Web/HTML",
    "CSS": "https://developer.mozilla.org/docs/Web/CSS", "PLpgSQL": "https://www.postgresql.org/docs/current/plpgsql.html",
    "R": "https://www.r-project.org", "TypeScript": "https://www.typescriptlang.org",
    "Swift": "https://www.swift.org", "C": "https://en.cppreference.com/w/c",
    "Dockerfile": "https://docs.docker.com/reference/dockerfile/", "Makefile": "https://www.gnu.org/software/make/",
}

QUERY = """query($after: String) { viewer { login repositories(first: 100, after: $after, ownerAffiliations: OWNER) {
  pageInfo { hasNextPage endCursor }
  nodes { name isFork isPrivate languages(first: 50) { edges { size node { name color } } } } } } }"""


def gql(token, variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": "lang-card"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.load(r)
    if "errors" in body:
        sys.exit(f"GraphQL error: {body['errors']}")
    return body["data"]["viewer"]


def collect(token):
    owner, repos, after = None, [], None
    while True:
        v = gql(token, {"after": after})
        owner = v["login"]
        page = v["repositories"]
        repos += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    langs, used = {}, 0
    for r in repos:
        if r["isFork"] or r["name"] in EXCLUDE:
            continue
        edges = r["languages"]["edges"]
        used += bool(edges)
        for e in edges:
            d = langs.setdefault(e["node"]["name"], {"bytes": 0, "repos": 0, "private": 0,
                                                     "color": e["node"]["color"] or OTHER_COLOR})
            d["bytes"] += e["size"]; d["repos"] += 1; d["private"] += r["isPrivate"]
    return owner, used, langs


def fmt_bytes(n):
    return f"{n/1e6:.1f} MB" if n >= 1e6 else f"{n/1e3:.0f} KB"


def build_items(langs):
    total = sum(d["bytes"] for d in langs.values())
    ranked = sorted(langs.items(), key=lambda kv: -kv[1]["bytes"])
    items = [dict(name=k, pct=d["bytes"] / total * 100, **d) for k, d in ranked[:TOP_N]]
    rest = ranked[TOP_N:]
    if rest:
        b = sum(d["bytes"] for _, d in rest)
        items.append(dict(name="Other", pct=b / total * 100, bytes=b, color=OTHER_COLOR,
                          repos=None, private=None,
                          members=[(k, d["bytes"] / total * 100) for k, d in rest]))
    return total, items


def keyframes(windows, on, off, e):
    """SMIL values/keyTimes: `on` inside each [a,b] window (fading over e), `off` elsewhere."""
    merged = []
    for a, b in sorted(windows):
        if merged and abs(merged[-1][1] - a) < 1e-9:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    pts = []
    for a, b in merged:
        pts += [(0.0, on)] if a <= 0 else [(a, off), (a + e, on)]
        pts += [(1.0, on)] if b >= 1 else [(b - e, on), (b, off)]
    if pts[0][0] > 0:
        pts.insert(0, (0.0, off))
    if pts[-1][0] < 1:
        pts.append((1.0, off))
    kt = ";".join(f"{t:.4f}" for t, _ in pts)
    vals = ";".join(str(v) for _, v in pts)
    return vals, kt


def donut_svg(items, total, n_repos, theme):
    c = THEMES[theme]
    cx = cy = 160; r = 110; sw = 34; C = 2 * math.pi * r
    W = len(items) + 1                      # slide 0 = overview, then one per language
    step = 2.4; T = W * step; e = 0.25 / T  # 0.25 s fades
    sweep_end = 0.2 + 0.12 * len(items) + 0.7
    begin = f"{sweep_end:.2f}s"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 320" width="320" height="320" '
           f'font-family="{FONT}" role="img" aria-label="Language share">']
    start = 0.0
    for k, it in enumerate(items, 1):
        seg = it["pct"] / 100 * C
        dash = max(seg - 2.0, 0.6)          # 2px gap between segments
        rot = -90 + start / C * 360
        a, b = k / W, (k + 1) / W
        ov, okt = keyframes([(0, 1 / W), (a, b)], 1, c["dim"], e)
        wv, wkt = keyframes([(a, b)], sw + 12, sw, e)
        out.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{it["color"]}" stroke-width="{sw}" '
            f'stroke-dasharray="0 {C:.2f}" transform="rotate({rot:.3f} {cx} {cy})">'
            f'<animate attributeName="stroke-dasharray" from="0 {C:.2f}" to="{dash:.2f} {C:.2f}" '
            f'dur="0.7s" begin="{0.2 + 0.12 * (k - 1):.2f}s" fill="freeze" calcMode="spline" '
            f'keyTimes="0;1" keySplines="0.2 0.8 0.2 1"/>'
            f'<animate attributeName="stroke-opacity" values="{ov}" keyTimes="{okt}" dur="{T:.1f}s" '
            f'begin="{begin}" repeatCount="indefinite"/>'
            f'<animate attributeName="stroke-width" values="{wv}" keyTimes="{wkt}" dur="{T:.1f}s" '
            f'begin="{begin}" repeatCount="indefinite"/></circle>')
        start += seg

    def label(title, big, sub, dot, windows, base):
        v, kt = keyframes(windows, 1, 0, e)
        dot_svg = (f'<circle cx="{cx - 6 - len(title) * 4.1:.1f}" cy="{cy - 30}" r="5" fill="{dot}"/>'
                   if dot else "")
        return (f'<g opacity="{base}">{dot_svg}'
                f'<text x="{cx + (6 if dot else 0)}" y="{cy - 25}" text-anchor="middle" font-size="15" '
                f'font-weight="600" fill="{c["fg"]}">{html.escape(title)}</text>'
                f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="36" font-weight="700" '
                f'fill="{c["fg"]}">{html.escape(big)}</text>'
                f'<text x="{cx}" y="{cy + 38}" text-anchor="middle" font-size="12" '
                f'fill="{c["muted"]}">{html.escape(sub)}</text>'
                f'<animate attributeName="opacity" values="{v}" keyTimes="{kt}" dur="{T:.1f}s" '
                f'begin="{begin}" repeatCount="indefinite"/></g>')

    out.append(label("All languages", fmt_bytes(total), f"of code · {n_repos} repos", None, [(0, 1 / W)], 1))
    for k, it in enumerate(items, 1):
        sub = (f"{fmt_bytes(it['bytes'])} · {it['repos']} repo{'s' * (it['repos'] != 1)}"
               if it["repos"] else f"{fmt_bytes(it['bytes'])} · {len(it['members'])} languages")
        out.append(label(it["name"], f"{it['pct']:.1f}%", sub, it["color"], [(k / W, (k + 1) / W)], 0))
    out.append("</svg>")
    return "".join(out)


def row_svg(it, rank, max_pct, theme):
    c = THEMES[theme]
    bx, bw = 150, 214
    fill = max(it["pct"] / max_pct * bw, 3)
    d = 0.15 + 0.08 * rank
    name = it["name"] if it["repos"] else f"Other ({len(it['members'])})"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 440 30" width="440" height="30" '
            f'font-family="{FONT}" role="img" aria-label="{html.escape(name)} {it["pct"]:.1f}%">'
            f'<circle cx="10" cy="15" r="6" fill="{it["color"]}"/>'
            f'<text x="24" y="20" font-size="14" font-weight="600" fill="{c["fg"]}">{html.escape(name)}</text>'
            f'<rect x="{bx}" y="11" width="{bw}" height="8" rx="4" fill="{c["track"]}"/>'
            f'<rect x="{bx}" y="11" width="0" height="8" rx="4" fill="{it["color"]}">'
            f'<animate attributeName="width" from="0" to="{fill:.2f}" dur="0.9s" begin="{d:.2f}s" fill="freeze" '
            f'calcMode="spline" keyTimes="0;1" keySplines="0.2 0.8 0.2 1"/></rect>'
            f'<text x="436" y="20" text-anchor="end" font-size="14" font-weight="700" '
            f'fill="{c["fg"]}">{it["pct"]:.1f}%</text></svg>')


def tooltip(it):
    if it["repos"] is None:
        parts = ", ".join(f"{n} {p:.1f}%" for n, p in it["members"][:8])
        more = f" +{len(it['members']) - 8} more" if len(it["members"]) > 8 else ""
        return f"Other · {it['pct']:.1f}% · {fmt_bytes(it['bytes'])} — {parts}{more}"
    return (f"{it['name']} · {it['pct']:.1f}% · {fmt_bytes(it['bytes'])} in {it['repos']} "
            f"repo{'s' * (it['repos'] != 1)} ({it['private']} private)")


def picture(owner, name, alt, width, ver):
    light = RAW.format(owner=owner, name=f"{name}.svg") + f"?v={ver}"
    dark = RAW.format(owner=owner, name=f"{name}-dark.svg") + f"?v={ver}"
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="{dark}">'
            f'<img alt="{html.escape(alt)}" src="{light}" width="{width}"></picture>')


def readme_block(owner, items, n_repos, ver):
    rows = []
    for i, it in enumerate(items, 1):
        href = LINKS.get(it["name"], f"https://github.com/topics/{it['name'].lower().replace(' ', '-')}")
        if it["repos"] is None:
            href = f"https://github.com/{owner}?tab=repositories"
        rows.append(f'<a href="{href}" title="{html.escape(tooltip(it))}">'
                    + picture(owner, f"lang-row-{i:02d}", f"{it['name']} {it['pct']:.1f}%", 400, ver) + "</a><br>")
    return ("<!--LANGS:START-->\n<table><tr>\n<td align=\"center\" valign=\"middle\">\n"
            + picture(owner, "lang-donut", "Language share across all repositories", 250, ver)
            + "\n</td>\n<td valign=\"middle\">\n" + "\n".join(rows) + "\n</td>\n</tr></table>\n"
            + f"<sub>Share of code by size across the {n_repos} repositories I own that contain code, public and private "
              "(forks excluded). Hover a row for details · updated daily.</sub>\n<!--LANGS:END-->")


def main():
    token = os.environ.get("LANG_STATS_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("LANG_STATS_TOKEN not set")
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "dist"); out.mkdir(parents=True, exist_ok=True)
    readme = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
    owner, n_repos, langs = collect(token)
    total, items = build_items(langs)
    stats = {"owner": owner, "repos": n_repos, "total_bytes": total,
             "items": [{k: v for k, v in it.items() if k != "members"} | ({"members": it["members"]} if "members" in it else {})
                       for it in items]}
    ver = hashlib.sha1((STYLE + json.dumps(stats, sort_keys=True)).encode()).hexdigest()[:8]
    for th in THEMES:
        (out / f"lang-donut{th}.svg").write_text(donut_svg(items, total, n_repos, th), encoding="utf-8")
        for i, it in enumerate(items, 1):
            (out / f"lang-row-{i:02d}{th}.svg").write_text(row_svg(it, i, items[0]["pct"], th), encoding="utf-8")
    (out / "lang-stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    if readme:
        s = readme.read_text(encoding="utf-8")
        a, b = s.find("<!--LANGS:START-->"), s.find("<!--LANGS:END-->")
        if a < 0 or b < 0:
            sys.exit("README markers <!--LANGS:START--> / <!--LANGS:END--> not found")
        readme.write_text(s[:a] + readme_block(owner, items, n_repos, ver) + s[b + len("<!--LANGS:END-->"):],
                          encoding="utf-8")
    print(f"{owner}: {n_repos} repos, {fmt_bytes(total)}, {len(items)} rows, v={ver}")
    for it in items:
        print(f"  {it['name']:12s} {it['pct']:5.1f}%  {tooltip(it)}")


if __name__ == "__main__":
    main()
