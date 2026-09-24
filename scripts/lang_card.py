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
STYLE = "4"  # bump when the SVG design changes, so GitHub's image cache refreshes
EXCLUDE = {"DouYinSparkFlow-Auto"} | {x for x in os.environ.get("LANG_EXCLUDE", "").split(",") if x}
RAW = "https://raw.githubusercontent.com/{owner}/{owner}/output/{name}"
OTHER_COLOR = "#8b949e"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"
THEMES = {
    "":      {"fg": "#1f2328", "muted": "#59636e", "track": "#eaeef2"},
    "-dark": {"fg": "#e6edf3", "muted": "#9198a1", "track": "#262c36"},
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
    langs, used, used_private = {}, 0, 0
    for r in repos:
        if r["isFork"] or r["name"] in EXCLUDE:
            continue
        edges = r["languages"]["edges"]
        used += bool(edges)
        used_private += bool(edges) and r["isPrivate"]
        for e in edges:
            d = langs.setdefault(e["node"]["name"], {"bytes": 0, "repos": 0, "private": 0,
                                                     "color": e["node"]["color"] or OTHER_COLOR})
            d["bytes"] += e["size"]; d["repos"] += 1; d["private"] += r["isPrivate"]
    return owner, used, used_private, langs


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


def grow(attr, start, end, delay, dur):
    """Animate attr from start to end after `delay`, starting at t=0 so there is no flash of the
    final state; the element's own attribute already holds `end`, so renderers that skip SMIL
    (mobile apps, link previews) still show the finished chart."""
    total = delay + dur
    return (f'<animate attributeName="{attr}" values="{start};{start};{end}" '
            f'keyTimes="0;{delay / total:.4f};1" dur="{total:.2f}s" begin="0s" fill="freeze" '
            f'calcMode="spline" keySplines="0 0 1 1;0.2 0.8 0.2 1"/>')


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
    """Donut with a liquid-glass lens that slides segment to segment: it stretches while moving,
    overshoots and settles; underneath it a thicker, slightly saturated copy of the ring shows
    through (refraction), with a soft shadow and a specular rim. The copy is static and only the
    mask moves, so browsers can cache the filter output."""
    c = THEMES[theme]; dark = theme == "-dark"
    cx = cy = 160; r = 110; sw = 34; C = 2 * math.pi * r
    M = len(items); W = M + 1; step = 2.4; T = W * step
    sweep_end = 0.2 + 0.12 * M + 0.7
    move, over = 0.55, 0.36                     # 滑动总时长 / 冲过头的时刻
    # 每个扇区的中心角与镜片长度(度)
    segs, start = [], 0.0
    for it in items:
        deg = it["pct"] / 100 * 360
        cap = math.degrees((sw + 12) / 2 / r)
        segs.append(dict(center=-90 + start + deg / 2, lens=min(max(deg - 2 * cap - 3, 0.5), 60), deg=deg))
        start += deg
    # 关键帧:(时间, 中心角, 镜片长度, 不透明度)
    kf = [(0.0, segs[0]["center"], segs[0]["lens"] * 0.6, 0.0),
          (step, segs[0]["center"], segs[0]["lens"] * 0.6, 0.0),
          (step + 0.35, segs[0]["center"], segs[0]["lens"], 1.0)]
    for k in range(1, M):
        a, b = segs[k - 1], segs[k]; t0 = (k + 1) * step
        d = b["center"] - a["center"]
        kf += [(t0, a["center"], a["lens"], 1.0),
               (t0 + 0.18, a["center"] + d * 0.45, (a["lens"] + b["lens"]) / 2 + abs(d) * 0.55, 1.0),   # 拉长
               (t0 + over, b["center"] + min(d * 0.10, 6), b["lens"] * 1.04, 1.0),                    # 冲过头
               (t0 + move, b["center"], b["lens"], 1.0)]                                               # 回弹落定
    last = segs[-1]
    kf += [(T - 0.35, last["center"], last["lens"], 1.0), (T, last["center"], last["lens"] * 0.6, 0.0)]
    kt = ";".join(f"{t / T:.5f}" for t, *_ in kf)
    splines = ";".join(["0.3 0 0.2 1"] * (len(kf) - 1))
    rot = ";".join(f"{ce - ln / 2:.3f} {cx} {cy}" for _, ce, ln, _ in kf)
    dash = ";".join(f"{ln / 360 * C:.2f} {C:.2f}" for _, _, ln, _ in kf)
    op = ";".join(f"{o}" for *_, o in kf)
    begin = f"{sweep_end:.2f}s"
    anim = (f'<animateTransform attributeName="transform" type="rotate" values="{rot}" keyTimes="{kt}" '
            f'calcMode="spline" keySplines="{splines}" dur="{T:.1f}s" begin="{begin}" repeatCount="indefinite"/>'
            f'<animate attributeName="stroke-dasharray" values="{dash}" keyTimes="{kt}" calcMode="spline" '
            f'keySplines="{splines}" dur="{T:.1f}s" begin="{begin}" repeatCount="indefinite"/>')
    opa = (f'<animate attributeName="opacity" values="{op}" keyTimes="{kt}" dur="{T:.1f}s" begin="{begin}" '
           f'repeatCount="indefinite"/>')
    r0 = kf[0]
    def capsule(width, extra="", shrink=0.0):
        L = max(r0[2] - shrink, 1)
        a = (anim if not shrink else anim.replace(dash, ";".join(f"{max(ln - shrink, 1) / 360 * C:.2f} {C:.2f}" for _, _, ln, _ in kf)))
        return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke-width="{width}" stroke-linecap="round" '
                f'stroke-dasharray="{L / 360 * C:.2f} {C:.2f}" transform="rotate({r0[1] - r0[2] / 2:.3f} {cx} {cy})" {extra}>{a}</circle>')
    tint = "#ffffff" if not dark else "#cfe3ff"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 320" width="320" height="320" font-family="{FONT}">',
           '<defs>',
           '<filter id="glass" filterUnits="userSpaceOnUse" x="0" y="0" width="320" height="320">'
           '<feGaussianBlur stdDeviation="0.6"/><feColorMatrix type="saturate" values="1.18"/>'
           '<feComponentTransfer><feFuncR type="linear" slope="1.05"/><feFuncG type="linear" slope="1.05"/>'
           '<feFuncB type="linear" slope="1.05"/></feComponentTransfer></filter>',
           '<filter id="shadow" filterUnits="userSpaceOnUse" x="0" y="0" width="320" height="320">'
           '<feGaussianBlur stdDeviation="4"/></filter>',
           f'<linearGradient id="rim" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="{0.95 if not dark else 0.8}"/>'
           f'<stop offset="0.5" stop-color="#fff" stop-opacity="0.25"/><stop offset="1" stop-color="#fff" stop-opacity="{0.55 if not dark else 0.35}"/></linearGradient>',
           '<mask id="lens" maskUnits="userSpaceOnUse" x="0" y="0" width="320" height="320">'
           + capsule(sw + 12, 'stroke="#fff"') + '</mask>',
           '<mask id="rimMask" maskUnits="userSpaceOnUse" x="0" y="0" width="320" height="320">'
           + capsule(sw + 12, 'stroke="#fff"') + capsule(sw + 8, 'stroke="#000"', shrink=1.2) + '</mask>',
           '</defs>',
           '<g id="ring">']
    start = 0.0; rings = []
    for k, it in enumerate(items, 1):
        seg = it["pct"] / 100 * C; dash_ = max(seg - 2.0, 0.6); rot_ = -90 + start / C * 360
        rings.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{it["color"]}" stroke-width="{sw}" '
                   f'stroke-dasharray="{dash_:.2f} {C:.2f}" transform="rotate({rot_:.3f} {cx} {cy})">'
                   + grow("stroke-dasharray", f"0 {C:.2f}", f"{dash_:.2f} {C:.2f}", 0.2 + 0.12 * (k - 1), 0.7) + '</circle>')
        out.append(rings[-1]); start += seg
    out.append('</g>')
    thick = ''.join(t.replace(f'stroke-width="{sw}"', f'stroke-width="{sw + 7}"').split('<animate')[0] + '</circle>' for t in rings)
    # 投影 → 折射副本(放大+饱和+微模糊)→ 玻璃着色 → 高光边缘;整组一起淡入淡出
    out.append(f'<g opacity="0">{opa}'
               f'<g transform="translate(0 3)" filter="url(#shadow)" opacity="{0.22 if not dark else 0.5}">'
               + capsule(sw + 12, 'stroke="#000"') + '</g>'
               f'<g mask="url(#lens)"><g filter="url(#glass)">' + thick + '</g>'
               f'<rect width="320" height="320" fill="{tint}" opacity="{0.16 if not dark else 0.10}"/></g>'
               f'<rect width="320" height="320" fill="url(#rim)" mask="url(#rimMask)"/>'
               '</g>')
    # 中心文字沿用原来的轮播
    e = 0.25 / T
    def label(title, big, sub, dot, windows, base):
        v, kt_ = keyframes(windows, 1, 0, e)
        dot_svg = f'<circle cx="{cx - 6 - len(title) * 4.1:.1f}" cy="{cy - 30}" r="5" fill="{dot}"/>' if dot else ""
        return (f'<g opacity="{base}">{dot_svg}<text x="{cx + (6 if dot else 0)}" y="{cy - 25}" text-anchor="middle" font-size="15" font-weight="600" fill="{c["fg"]}">{html.escape(title)}</text>'
                f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="36" font-weight="700" fill="{c["fg"]}">{html.escape(big)}</text>'
                f'<text x="{cx}" y="{cy + 38}" text-anchor="middle" font-size="12" fill="{c["muted"]}">{html.escape(sub)}</text>'
                f'<animate attributeName="opacity" values="{v}" keyTimes="{kt_}" dur="{T:.1f}s" begin="{begin}" repeatCount="indefinite"/></g>')
    out.append(label("All languages", fmt_bytes(total), f"of code · {n_repos} repos", None, [(0, 1 / W)], 1))
    for k, it in enumerate(items, 1):
        sub = (f"{fmt_bytes(it['bytes'])} · {it['repos']} repo{'s' * (it['repos'] != 1)}" if it.get("repos") else f"{fmt_bytes(it['bytes'])} · {len(it['members'])} languages")
        out.append(label(it["name"], f"{it['pct']:.1f}%", sub, it["color"], [(k / W, (k + 1) / W)], 0))
    out.append('</svg>')
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
            f'<rect x="{bx}" y="11" width="{fill:.2f}" height="8" rx="4" fill="{it["color"]}">'
            + grow("width", "0", f"{fill:.2f}", d, 0.9) + '</rect>'
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
    owner, n_repos, n_private, langs = collect(token)
    # A token scoped to public repos doesn't error — it just returns less. Refuse to shrink silently.
    prev_path = os.environ.get("PREV_STATS")
    if prev_path and os.path.exists(prev_path) and os.path.getsize(prev_path):
        prev = json.load(open(prev_path, encoding="utf-8"))
        if prev.get("private_repos", 0) > 0 and n_private == 0:
            sys.exit(f"token sees no private repos (last run saw {prev['private_repos']}); "
                     "check that it was created with 'All repositories'")
    total, items = build_items(langs)
    stats = {"owner": owner, "repos": n_repos, "private_repos": n_private, "total_bytes": total,
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
    print(f"{owner}: {n_repos} repos ({n_private} private), {fmt_bytes(total)}, {len(items)} rows, v={ver}")
    for it in items:
        print(f"  {it['name']:12s} {it['pct']:5.1f}%  {tooltip(it)}")


if __name__ == "__main__":
    main()
