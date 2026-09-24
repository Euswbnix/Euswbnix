#!/usr/bin/env python3
"""Post-process github-profile-3d-contrib output.

1. Remove its language pie: it only sees public repositories and full mode has no setting to
   hide it (our own language card replaces it). The pie is a top-level
   <g transform="translate(40, …)"> whose slices carry <title>Lang N</title>.
2. Put a streak panel in the space it leaves: current / longest streak, active days, busiest day.
   Same source as the 3D calendar (contributionCalendar), so private contributions count too.

A file is edited only when exactly one pie group is found. If the calendar can't be fetched the
pie is still removed and the panel is skipped.

Usage: post_3d.py <user> <svg>...   Env: GITHUB_TOKEN
"""
import datetime as dt, json, os, re, sys, urllib.request, xml.etree.ElementTree as ET

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
SLICE_TITLE = re.compile(r"^\S.* \d+$")          # "Python 250" — radar titles are bare numbers
QUERY = """query($login: String!) { user(login: $login) { contributionsCollection {
  contributionCalendar { weeks { contributionDays { date contributionCount } } } } } }"""


def is_pie(g):
    if not g.get("transform", "").replace(" ", "").startswith("translate(40,"):
        return False
    return any(SLICE_TITLE.match(t.text or "") for t in g.iter(f"{{{NS}}}title"))


def fetch_days(user, token):
    req = urllib.request.Request("https://api.github.com/graphql",
                                 data=json.dumps({"query": QUERY, "variables": {"login": user}}).encode(),
                                 headers={"Authorization": f"bearer {token}", "User-Agent": "post-3d"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.load(r)
    weeks = body["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    return sorted((dt.date.fromisoformat(d["date"]), d["contributionCount"])
                  for w in weeks for d in w["contributionDays"])


def streak_stats(days):
    best = cur = 0; best_span = None; run_start = None
    for d, c in days:
        if c > 0:
            cur += 1
            run_start = d if cur == 1 else run_start
            if cur > best:
                best, best_span = cur, (run_start, d)
        else:
            cur = 0
    i = len(days) - 1
    if days[i][1] == 0:               # today not over yet: an empty today doesn't break the streak
        i -= 1
    now = 0; now_start = None
    while i >= 0 and days[i][1] > 0:
        now += 1; now_start = days[i][0]; i -= 1
    busiest = max(days, key=lambda x: x[1])
    active = sum(c > 0 for _, c in days)
    return dict(now=now, now_start=now_start, best=best, best_span=best_span,
                active=active, total_days=len(days), busiest=busiest)


def md(d):
    return f"{d:%b} {d.day}"


def panel(s, accent):
    def tile(x, y, label, value, unit, sub):
        return (f'<rect x="{x}" y="{y - 22}" width="5" height="96" rx="2.5" fill="{accent}" opacity="0.85"/>'
                f'<text x="{x + 20}" y="{y}" class="fill-weak" style="font-size: 19px; letter-spacing: 1px;">{label}</text>'
                f'<text x="{x + 18}" y="{y + 50}" class="fill-strong" style="font-size: 50px; font-weight: bold;">{value}'
                f'<tspan class="fill-fg" style="font-size: 22px; font-weight: normal;" dx="8">{unit}</tspan></text>'
                f'<text x="{x + 20}" y="{y + 78}" class="fill-weak" style="font-size: 19px;">{sub}</text>')
    tiles = [
        tile(60, 572, "CURRENT STREAK", s["now"], "days",
             f"since {md(s['now_start'])}" + (" · ties best" if s["now"] and s["now"] == s["best"] else "")
             if s["now"] else "start one today"),
        tile(300, 572, "LONGEST STREAK", s["best"], "days",
             f"{md(s['best_span'][0])} – {md(s['best_span'][1])}" if s["best_span"] else "—"),
        tile(60, 696, "ACTIVE DAYS", s["active"], f"/ {s['total_days']}",
             f"{s['active'] / s['total_days'] * 100:.0f}% of the past year"),
        tile(300, 696, "BUSIEST DAY", s["busiest"][1], "contribs", f"{s['busiest'][0]:%b} {s['busiest'][0].day}, {s['busiest'][0].year}"),
    ]
    frag = (f'<g xmlns="{NS}" opacity="1">'
            '<animate attributeName="opacity" values="0;0;1" keyTimes="0;0.55;1" dur="2.2s" fill="freeze"/>'
            + "".join(tiles) + "</g>")
    return ET.fromstring(frag)


def main():
    user, files = sys.argv[1], sys.argv[2:]
    stats = None
    try:
        stats = streak_stats(fetch_days(user, os.environ["GITHUB_TOKEN"]))
        print("streaks:", {k: (str(v) if isinstance(v, (dt.date, tuple)) else v) for k, v in stats.items()})
    except Exception as e:                         # panel is optional; removing the pie is not
        print(f"calendar unavailable, skipping streak panel: {e}")
    status = 0
    for path in files:
        tree = ET.parse(path); root = tree.getroot()
        hits = [g for g in root.findall(f"{{{NS}}}g") if is_pie(g)]
        if len(hits) != 1:
            print(f"{path}: expected 1 pie group, found {len(hits)} — left unchanged")
            status = 1
            continue
        root.remove(hits[0])
        if stats:
            style = "".join(t.text or "" for t in root.iter(f"{{{NS}}}style"))
            m = re.search(r"\.radar\s*\{[^}]*?\bfill:\s*([^;]+);", style)
            root.append(panel(stats, m.group(1).strip() if m else "#8b949e"))
        tree.write(path, encoding="utf-8", xml_declaration=False)
        print(f"{path}: pie removed" + (", streak panel added" if stats else ""))
    sys.exit(status)


if __name__ == "__main__":
    main()
