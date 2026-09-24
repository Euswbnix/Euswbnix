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
STYLE = "10"  # bump when the SVG design changes, so GitHub's image cache refreshes
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
    """Donut with a liquid-glass lens that moves segment to segment (modelled on the iOS tab bar).

    At rest the lens is the exact annular sector of the selected segment. To move, the lens centre
    glides along the ring in one smooth ease-in-out while, independently, the shape swells into a
    clear capsule much taller than the ring (round ends, near-parallel sides) and shrinks back to the
    exact target sector. Position and shape are separate animations with few keyframes each, so
    velocity never stalls mid-move. Inside the lens a magnified band of the ring (pre-computed,
    slightly richer colours) shows through; the edge is a thin translucent highlight. No filters,
    and a clipPath instead of a mask, so each frame only re-rasterises one morphing path.
    """
    c = THEMES[theme]; dark = theme == "-dark"
    cx = cy = 160; r = 110; sw = 34; C = 2 * math.pi * r
    M = len(items); W = M + 1; step = 2.4; T = W * step
    sweep_end = 0.2 + 0.12 * M + 0.7
    px2deg = 360 / C
    MOVE = 0.62                                            # 一次切换的时长

    segs, start = [], 0.0
    for it in items:
        seg = it["pct"] / 100 * C
        dash = max(seg - 2.0, 0.6)                          # 与下面画扇区完全同一算法
        s0 = -90 + start * px2deg; sp = dash * px2deg
        segs.append(dict(s=s0, span=sp, mid=s0 + sp / 2))
        start += seg

    # ---- 形状:以角度 0 为中心、从 -span/2 到 +span/2;h_end/h_mid 端部/中部厚度,cap 0=平头 1=半圆头
    def shape(span, h_end, h_mid, cap):
        a1 = math.radians(max(span, 0.05)); off = -a1 / 2

        def R(a, re, rm):
            return re + (rm - re) * math.sin(math.pi * a / a1)

        def dR(a, re, rm):
            return (rm - re) * math.pi / a1 * math.cos(math.pi * a / a1)

        def P(a, rad):
            return (cx + rad * math.cos(a + off), cy + rad * math.sin(a + off))

        def edge(re, rm, a0, a3):
            da = a3 - a0
            f = (4 / 3) * math.tan(da / 4) / da             # 圆弧精确的控制柄比例
            p0, p3 = P(a0, R(a0, re, rm)), P(a3, R(a3, re, rm))

            def d(a):
                rr, dr, th = R(a, re, rm), dR(a, re, rm), a + off
                return (dr * math.cos(th) - rr * math.sin(th), dr * math.sin(th) + rr * math.cos(th))
            d0, d3 = d(a0), d(a3)
            return ((p0[0] + d0[0] * f * da, p0[1] + d0[1] * f * da),
                    (p3[0] - d3[0] * f * da, p3[1] - d3[1] * f * da), p3)

        def capc(pa, pb, tang, h):
            k = 0.667 * h * cap
            s3 = ((pb[0] - pa[0]) / 3 * (1 - cap), (pb[1] - pa[1]) / 3 * (1 - cap))
            return ((pa[0] + s3[0] + tang[0] * k, pa[1] + s3[1] + tang[1] * k),
                    (pb[0] - s3[0] + tang[0] * k, pb[1] - s3[1] + tang[1] * k), pb)

        ro_e, ro_m, ri_e, ri_m = r + h_end / 2, r + h_mid / 2, r - h_end / 2, r - h_mid / 2
        te, ts = a1 + off, off
        o0 = P(0, ro_e)
        parts = [edge(ro_e, ro_m, 0, a1 / 2), edge(ro_e, ro_m, a1 / 2, a1),
                 capc(P(a1, ro_e), P(a1, ri_e), (-math.sin(te), math.cos(te)), h_end),
                 edge(ri_e, ri_m, a1, a1 / 2), edge(ri_e, ri_m, a1 / 2, 0),
                 capc(P(0, ri_e), o0, (math.sin(ts), -math.cos(ts)), h_end)]
        fmt = lambda p: f"{p[0]:.2f},{p[1]:.2f}"
        return f"M{fmt(o0)}" + "".join(f"C{fmt(a)} {fmt(b)} {fmt(e)}" for a, b, e in parts) + "Z"

    rest_shape = lambda g: (g["span"], sw, sw, 0.0)

    def swollen(a, b):                                      # 滑行中的胶囊:比圆环高得多、两端半圆
        stretch = (a["span"] + b["span"]) / 2 + 0.35 * abs(b["mid"] - a["mid"]) + 8
        return (stretch, sw + 24, sw + 28, 1.0)

    EASE, HOLD, HUMP = "0.42 0 0.18 1", "0 0 1 1", "0.35 0 0.35 1"
    pos = [(0.0, segs[0]["mid"], HOLD)]                    # (时间, 中心角, 进入下一段用的曲线)
    shp = [(0.0, rest_shape(segs[0]), HOLD)]
    opa = [(0.0, 0.0), (step, 0.0), (step + 0.2, 1.0)]    # 首次出现:直接淡入在 Java 上,不做缩放
    for k in range(1, M):
        a, b = segs[k - 1], segs[k]; t0 = (k + 1) * step
        pos += [(t0, a["mid"], EASE), (t0 + MOVE, b["mid"], HOLD)]
        shp += [(t0, rest_shape(a), HUMP), (t0 + MOVE * 0.45, swollen(a, b), HUMP), (t0 + MOVE, rest_shape(b), HOLD)]
    pos.append((T, segs[-1]["mid"], HOLD))
    shp.append((T, rest_shape(segs[-1]), HOLD))
    opa += [(T - 0.35, 1.0), (T, 0.0)]

    def smil(attr, frames, fmt_v, tag="animate", extra=""):
        kt = ";".join(f"{t / T:.5f}" for t, *_ in frames)
        vals = ";".join(fmt_v(f) for f in frames)
        spl = ";".join(f[2] for f in frames[:-1])
        return (f'<{tag} attributeName="{attr}" {extra} values="{vals}" keyTimes="{kt}" calcMode="spline" '
                f'keySplines="{spl}" dur="{T:.1f}s" begin="{sweep_end:.2f}s" repeatCount="indefinite"/>')

    lens_def = (f'<path id="lensShape" d="{shape(*shp[0][1])}" transform="rotate({pos[0][1]:.3f} {cx} {cy})">'
                + smil("transform", pos, lambda f: f"{f[1]:.3f} {cx} {cy}", "animateTransform", 'type="rotate"')
                + smil("d", shp, lambda f: shape(*f[1])) + "</path>")
    opa_anim = (f'<animate attributeName="opacity" values="{";".join(str(o) for _, o in opa)}" '
                f'keyTimes="{";".join(f"{t / T:.5f}" for t, _ in opa)}" dur="{T:.1f}s" begin="{sweep_end:.2f}s" '
                'repeatCount="indefinite"/>')

    def rich(hexc):                                         # 预先算好的"透过玻璃"颜色:饱和 ×1.18、提亮 ×1.05
        h = hexc.lstrip("#"); rr, gg, bb = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        l = 0.2126 * rr + 0.7152 * gg + 0.0722 * bb
        out_ = [min(1.0, max(0.0, (l + (v - l) * 1.18) * 1.05)) for v in (rr, gg, bb)]
        return "#" + "".join(f"{round(v * 255):02x}" for v in out_)

    tint = "#ffffff" if not dark else "#cfe3ff"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 320" width="320" height="320" '
           f'font-family="{FONT}" role="img" aria-label="Language share">',
           '<defs>', lens_def, '<clipPath id="lens"><use href="#lensShape"/></clipPath>', '</defs>',
           '<g id="ring">']
    start = 0.0; band = []
    for k, it in enumerate(items, 1):
        seg = it["pct"] / 100 * C; dash_ = max(seg - 2.0, 0.6); rot_ = -90 + start / C * 360
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{it["color"]}" stroke-width="{sw}" '
                   f'stroke-dasharray="{dash_:.2f} {C:.2f}" transform="rotate({rot_:.3f} {cx} {cy})">'
                   + grow("stroke-dasharray", f"0 {C:.2f}", f"{dash_:.2f} {C:.2f}", 0.2 + 0.12 * (k - 1), 0.7)
                   + '</circle>')
        # 放大后的色带:比扇区粗(放大感),又比鼓起的胶囊细(露出上下透明的玻璃)
        band.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{rich(it["color"])}" '
                    f'stroke-width="{sw + 10}" stroke-dasharray="{dash_:.2f} {C:.2f}" '
                    f'transform="rotate({rot_:.3f} {cx} {cy})"/>')
        start += seg
    out.append('</g>')
    hair = "#000" if not dark else "#fff"
    out.append(f'<g opacity="0">{opa_anim}'
               '<g clip-path="url(#lens)">'
               f'<rect width="320" height="320" fill="{tint}" opacity="{0.5 if not dark else 0.07}"/>'
               + "".join(band)
               + f'<rect width="320" height="320" fill="{tint}" opacity="{0.14 if not dark else 0.08}"/></g>'
               # 玻璃边缘:半透明细线,不是实心描边
               f'<use href="#lensShape" fill="none" stroke="{hair}" stroke-opacity="{0.12 if not dark else 0.22}" stroke-width="1.6"/>'
               f'<use href="#lensShape" fill="none" stroke="#fff" stroke-opacity="{0.75 if not dark else 0.4}" stroke-width="0.8"/>'
               '</g>')
    e = 0.25 / T

    def label(title, big, sub, dot, windows, base):
        v, kt_ = keyframes(windows, 1, 0, e)
        dot_svg = f'<circle cx="{cx - 6 - len(title) * 4.1:.1f}" cy="{cy - 30}" r="5" fill="{dot}"/>' if dot else ""
        return (f'<g opacity="{base}">{dot_svg}<text x="{cx + (6 if dot else 0)}" y="{cy - 25}" text-anchor="middle" '
                f'font-size="15" font-weight="600" fill="{c["fg"]}">{html.escape(title)}</text>'
                f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="36" font-weight="700" '
                f'fill="{c["fg"]}">{html.escape(big)}</text>'
                f'<text x="{cx}" y="{cy + 38}" text-anchor="middle" font-size="12" fill="{c["muted"]}">{html.escape(sub)}</text>'
                f'<animate attributeName="opacity" values="{v}" keyTimes="{kt_}" dur="{T:.1f}s" begin="{sweep_end:.2f}s" '
                'repeatCount="indefinite"/></g>')

    out.append(label("All languages", fmt_bytes(total), f"of code · {n_repos} repos", None, [(0, 1 / W)], 1))
    for k, it in enumerate(items, 1):
        sub = (f"{fmt_bytes(it['bytes'])} · {it['repos']} repo{'s' * (it['repos'] != 1)}" if it.get("repos")
               else f"{fmt_bytes(it['bytes'])} · {len(it['members'])} languages")
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
