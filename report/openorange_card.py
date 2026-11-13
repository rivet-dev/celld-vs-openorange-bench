"""Draws the OpenOrange vs celld social card (16:9, 3200x1800) for one report run.

Reads `report/<run>/summary.json` (one point per agent count) and `report/<run>/minutes.csv`
(five measured minutes per point, drawn as dots) and writes `report/<run>/openorange-vs-celld.png`.
OpenOrange is built on Rivet's storage engine, so its line is the benchmark's Rivet result.
Needs headless Chromium on PATH as `chromium` and network access for Google Fonts.

Usage: python3 report/openorange_card.py scale-25k-20261108
"""

import base64
import csv
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE / sys.argv[1]
W, H = 1600, 1080

OO_LOGO = base64.b64encode((HERE / "assets" / "openorange-logo.png").read_bytes()).decode()

PAPER, SURFACE, LINE, INK, SOFT, FAINT = "#f9f8f7", "#ffffff", "#e9e6e3", "#1a1918", "#44403c", "#78716c"
ORANGE, ORANGE_DEEP, ORANGE_SOFT = "#f26b1d", "#c4500f", "#fff1e8"
GREEN, GREEN_DEEP, GREEN_TINT = "#10803f", "#0b652f", "#d6efdf"
COLOR = {"oo": ORANGE, "cd": GREEN}
DEEP = {"oo": ORANGE_DEEP, "cd": GREEN_DEEP}
# Line-end cost figures: red for the expensive side.
COST_COLOR = {"oo": INK, "cd": "#d92d20"}

# Benchmark data: platform "rivet" is OpenOrange's storage, "celld" is celld.
summary = json.loads((RUN / "summary.json").read_text())["results"]
POINTS = {"oo": [], "cd": []}
for row in summary:
	POINTS["oo" if row["platform"] == "rivet" else "cd"].append((row["actors"], row["monthly_usd"]))
for points in POINTS.values():
	points.sort()

MINUTES = {"oo": [], "cd": []}
with open(RUN / "minutes.csv") as f:
	for row in csv.DictReader(f):
		key = "oo" if row["platform"] == "rivet" else "cd"
		MINUTES[key].append((int(row["held_agents"]), float(row["monthly_equivalent_usd"])))

TOP_AGENTS = POINTS["cd"][-1][0]
OO_TOP, CD_TOP = POINTS["oo"][-1][1], POINTS["cd"][-1][1]
RATIO = CD_TOP / OO_TOP

# The card highlights this agent count with a dotted line, its costs and its multiple.
MARK = 10000
MARK_COST = {key: dict(points)[MARK] for key, points in POINTS.items()}
MARK_RATIO = MARK_COST["cd"] / MARK_COST["oo"]

LIFE = "Each agent lives 60 s, writes a SQLite row every 10 s, then sleeps."
V1_FOOT = "Monthly S3 request cost, lower is better; highlighted at 10,000 concurrent agents. Ramped to 25,000 concurrent agents; each agent lives 60 s and writes to SQLite every 10 s.<br>S3 request cost only, not hardware. Identical hardware for both."
FOOT = "Measured Azure Blob requests, priced at S3 rates. Same SQLite workload, same machines, 6 to 24 nodes."

HEAD = f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;700&family=Schibsted+Grotesk:wght@600;700;800&display=swap">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: {W}px; height: {H}px; background: {PAPER}; color: {INK}; font-family: 'Inter', sans-serif; -webkit-font-smoothing: antialiased; overflow: hidden; }}
.frame {{ width: {W}px; height: {H}px; padding: 60px 64px; display: flex; flex-direction: column; }}
.mono {{ font-family: 'JetBrains Mono', monospace; }}
.lockup {{ display: flex; align-items: center; gap: 22px; }}
.oo {{ display: flex; align-items: center; gap: 14px; font-weight: 600; letter-spacing: -0.02em; }}
.oo b {{ color: {ORANGE}; font-weight: 600; }}
.vs {{ color: {FAINT}; font-weight: 500; }}
.cd {{ display: flex; align-items: center; gap: 12px; font-family: 'Schibsted Grotesk', sans-serif; font-weight: 800; color: {INK}; letter-spacing: -0.02em; }}
.sub {{ color: {SOFT}; }}
.foot {{ color: {FAINT}; font-size: 20px; }}
</style></head><body>"""


def celld_mark(size):
	"""celld's speed-lines mark: five bars fading from half to full accent, left to right."""
	bars = "".join(f'<rect x="150" y="{y}" width="600" height="50"/>' for y in (200, 300, 400, 500, 600))
	return (
		f'<svg width="{size * 4 / 3:.0f}" height="{size}" viewBox="150 200 600 450"><defs><linearGradient id="cdfade" gradientUnits="userSpaceOnUse" x1="150" y1="0" x2="750" y2="0">'
		f'<stop offset="0" stop-color="{GREEN}" stop-opacity=".5"/><stop offset="1" stop-color="{GREEN}"/></linearGradient></defs>'
		f'<g fill="url(#cdfade)">{bars}</g></svg>'
	)


def logo_uris():
	"""Data URIs for the line-end logos: OpenOrange's PNG and celld's mark as a standalone SVG."""
	bars = "".join(f'<rect x="150" y="{y}" width="600" height="50"/>' for y in (200, 300, 400, 500, 600))
	mark = (
		'<svg xmlns="http://www.w3.org/2000/svg" viewBox="150 125 600 600"><defs><linearGradient id="f" gradientUnits="userSpaceOnUse" x1="150" y1="0" x2="750" y2="0">'
		f'<stop offset="0" stop-color="{GREEN}" stop-opacity=".5"/><stop offset="1" stop-color="{GREEN}"/></linearGradient></defs>'
		f'<g fill="url(#f)">{bars}</g></svg>'
	)
	return {"oo": f"data:image/png;base64,{OO_LOGO}", "cd": "data:image/svg+xml;base64," + base64.b64encode(mark.encode()).decode()}


def lockup(size):
	"""celld vs OpenOrange, logo-forward: large marks with small wordmarks."""
	text = size * 0.72
	return (
		f'<div class="lockup" style="font-size:{text:.0f}px;gap:26px">'
		f'<span class="cd" style="gap:14px">{celld_mark(size * 1.05)}celld</span>'
		f'<span class="vs" style="font-size:{text * 0.8:.0f}px">vs</span>'
		f'<span class="oo" style="gap:14px"><img src="data:image/png;base64,{OO_LOGO}" style="width:{size * 1.7:.0f}px;height:{size * 1.7:.0f}px"><span>Open<b>Orange</b></span></span></div>'
	)


def money(v):
	if v >= 1000:
		return f"${v:,.0f}"
	if v >= 10:
		return f"${v:,.0f}"
	return f"${v:.2f}"


def axis_money(v):
	return f"${v / 1000:g}K" if v >= 1000 else f"${v:g}"


def agents_label(n):
	return f"{n / 1000:g}K" if n >= 1000 else f"{n:g}"


LOGO_URI = logo_uris()


def chart(w, h, *, log_x=False, log_y=False, per_agent=False, gap_fill=False, y_max=None, y_ticks=None, x_ticks=None, end_labels=True, dot_r=4.5, raw=True, mark=None):
	"""Line chart of both platforms with every measured minute as a translucent dot."""
	pad_l, pad_r, pad_t, pad_b = 92, 210 if end_labels else 30, 20, 56
	pw, ph = w - pad_l - pad_r, h - pad_t - pad_b

	def value(agents, usd):
		return usd / agents if per_agent else usd

	all_x = [a for p in POINTS.values() for a, _ in p]
	x_lo, x_hi = (min(all_x), max(all_x)) if log_x else (0, max(all_x))
	ys = [value(a, u) for p in POINTS.values() for a, u in p]
	y_hi = y_max or max(ys)
	y_lo = (0.008 if per_agent else min(ys) * 0.6) if log_y else 0

	def px(a):
		if log_x:
			return pad_l + (math.log10(a) - math.log10(x_lo)) / (math.log10(x_hi) - math.log10(x_lo)) * pw
		return pad_l + a / x_hi * pw

	def py(v):
		if log_y:
			return pad_t + ph - (math.log10(v) - math.log10(y_lo)) / (math.log10(y_hi) - math.log10(y_lo)) * ph
		return pad_t + ph - min(v, y_hi) / y_hi * ph

	out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="overflow:visible">']
	for t in y_ticks or []:
		out.append(f'<line x1="{pad_l}" x2="{pad_l + pw}" y1="{py(t):.1f}" y2="{py(t):.1f}" stroke="{LINE}" stroke-width="2"/>')
		label = (f"${t:g}" if t >= 1 else f"${t:.2f}") if per_agent else axis_money(t)
		out.append(f'<text x="{pad_l - 16}" y="{py(t) + 7:.1f}" text-anchor="end" font-size="20" fill="{FAINT}" font-family="JetBrains Mono">{label}</text>')
	for t in x_ticks or []:
		out.append(f'<text x="{px(t):.1f}" y="{pad_t + ph + 36}" text-anchor="middle" font-size="20" fill="{FAINT}" font-family="Inter">{agents_label(t)}</text>')
	out.append(f'<text x="{pad_l + pw / 2:.1f}" y="{pad_t + ph + 56 + 14}" text-anchor="middle" font-size="19" fill="{FAINT}" font-family="Inter">concurrent agents</text>')

	if gap_fill:
		top = [f"{px(a):.1f},{py(value(a, u)):.1f}" for a, u in POINTS["cd"]]
		bottom = [f"{px(a):.1f},{py(value(a, u)):.1f}" for a, u in reversed(POINTS["oo"])]
		out.append(f'<polygon points="{" ".join(top + bottom)}" fill="{ORANGE_SOFT}"/>')

	for key in (("cd", "oo") if raw else ()):
		for a, u in MINUTES[key]:
			out.append(f'<circle cx="{px(a):.1f}" cy="{py(value(a, u)):.1f}" r="{dot_r}" fill="{COLOR[key]}" fill-opacity="0.28"/>')
	for key in ("cd", "oo"):
		path = " ".join(f"{'M' if i == 0 else 'L'}{px(a):.1f},{py(value(a, u)):.1f}" for i, (a, u) in enumerate(POINTS[key]))
		out.append(f'<path d="{path}" fill="none" stroke="{COLOR[key]}" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>')

		# One marker per agent count the benchmark measured.
		for a, u in POINTS[key][:-1]:
			out.append(f'<circle cx="{px(a):.1f}" cy="{py(value(a, u)):.1f}" r="6.5" fill="{COLOR[key]}" stroke="{PAPER}" stroke-width="3"/>')
		a, u = POINTS[key][-1]
		out.append(f'<circle cx="{px(a):.1f}" cy="{py(value(a, u)):.1f}" r="9" fill="{COLOR[key]}" stroke="{PAPER}" stroke-width="4"/>')
		if end_labels:
			name = "celld" if key == "cd" else "OpenOrange"
			shown = (f"${value(a, u):.3f}" if value(a, u) < 0.1 else f"${value(a, u):.2f}") if per_agent else money(u)
			unit = "/agent" if per_agent else "/mo"
			logo = LOGO_URI[key]
			out.append(f'<image x="{px(a) + 22:.1f}" y="{py(value(a, u)) - 24:.1f}" width="46" height="46" href="{logo}"/>')
			out.append(f'<text x="{px(a) + 78:.1f}" y="{py(value(a, u)) - 6:.1f}" font-size="18" fill="{SOFT}" font-family="Inter" font-weight="500">{name}</text>')
			out.append(f'<text x="{px(a) + 78:.1f}" y="{py(value(a, u)) + 22:.1f}" font-size="26" fill="{COST_COLOR[key]}" font-family="JetBrains Mono" font-weight="700">{shown}<tspan font-size="18" fill="{FAINT}">{unit}</tspan></text>')

	# Dotted line at the highlighted agent count, with each platform's cost beside its point.
	if mark is not None:
		mx = px(mark)
		out.append(f'<line x1="{mx:.1f}" x2="{mx:.1f}" y1="{pad_t - 6}" y2="{py(0):.1f}" stroke="{SOFT}" stroke-width="2.5" stroke-dasharray="3 8" stroke-linecap="round"/>')
		out.append(f'<text x="{mx:.1f}" y="{pad_t - 18}" text-anchor="middle" font-size="20" font-weight="600" fill="{SOFT}" font-family="Inter">{agents_label(mark)} agents</text>')
		for key in ("cd", "oo"):
			u = dict(POINTS[key])[mark]
			out.append(f'<circle cx="{mx:.1f}" cy="{py(u):.1f}" r="10" fill="{COLOR[key]}" stroke="{PAPER}" stroke-width="4"/>')
			out.append(f'<text x="{mx - 18:.1f}" y="{py(u) - 18:.1f}" text-anchor="end" font-size="30" fill="{COST_COLOR[key]}" font-family="JetBrains Mono" font-weight="700">{money(u)}<tspan font-size="19" fill="{FAINT}">/mo</tspan></text>')
	out.append("</svg>")
	return "".join(out)


def ratio_badge(size, label="cheaper"):
	return f'<span style="font-size:{size}px;font-weight:700;color:{ORANGE};letter-spacing:-0.035em;line-height:1">{RATIO:.1f}×</span> <span style="font-size:{size * 0.32:.0f}px;font-weight:600;color:{SOFT}">{label}</span>'


def legend(raw=True):
	return (
		f'<div style="display:flex;gap:30px;font-size:20px;color:{SOFT};font-weight:500;align-items:center">'
		f'<span style="display:flex;align-items:center;gap:10px"><i style="width:26px;height:6px;border-radius:3px;background:{GREEN};display:block"></i>celld</span>'
		f'<span style="display:flex;align-items:center;gap:10px"><i style="width:26px;height:6px;border-radius:3px;background:{ORANGE};display:block"></i>OpenOrange</span>'
		+ (f'<span style="display:flex;align-items:center;gap:10px"><i style="width:12px;height:12px;border-radius:50%;background:{FAINT};opacity:.45;display:block"></i>each measured minute</span>' if raw else '')
		+ '</div>'
	)


def v1():
	"""Classic: lockup, ratio headline, linear chart with raw minutes."""
	body = chart(1472, 630, raw=False, mark=MARK, y_max=25000, y_ticks=[0, 5000, 10000, 15000, 20000, 25000], x_ticks=[0, 5000, 10000, 15000, 20000, 25000])
	return f"""<div class="frame" style="padding-bottom:34px">
{lockup(44)}
<div style="margin-top:22px;display:flex;align-items:baseline;gap:20px"><span style="font-size:112px;font-weight:700;color:{ORANGE};letter-spacing:-0.045em;line-height:1">{MARK_RATIO:.1f}×</span><span style="font-size:36px;font-weight:600;letter-spacing:-0.015em">lower S3 request cost</span></div>
<div style="margin-top:56px">{body}</div>
<div class="foot" style="margin-top:auto;font-size:15px;color:#a8a29e;line-height:1.5">{V1_FOOT}</div></div>"""


page = RUN / "openorange-vs-celld.render.html"
page.write_text(HEAD + v1() + "</body></html>")
png = RUN / "openorange-vs-celld.png"
subprocess.run(
	["chromium", "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
	 f"--window-size={W},{H}", "--virtual-time-budget=6000", f"--screenshot={png}", f"file://{page}"],
	check=True, stderr=subprocess.DEVNULL, timeout=90,
)
page.unlink()
print("wrote", png)
