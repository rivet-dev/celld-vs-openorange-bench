#!/usr/bin/env python3
"""Draw the headline cost chart as a 1200x675 (16:9) SVG sized for social posts.

Reads only `report/<run>/summary.json`, which `report/build.py` writes, and writes
`report/<run>/social.svg`. Pass `--png` to also render `social.png` at 2x with a
local headless Chromium.
"""
import argparse
import json
import shutil
import subprocess
import tempfile
from html import escape
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

WIDTH = 1200
HEIGHT = 675

# Same light theme tokens as report/template.html.
PAGE = '#f9f9f7'
INK = '#0b0b0b'
SECONDARY = '#55534e'
MUTED = '#898781'
GRID = '#e1e0d9'
AXIS = '#c3c2b7'
COLORS = {'celld': '#eb6834', 'rivet': '#2a78d6'}
NAMES = {'celld': 'celld Durable Objects', 'rivet': 'Rivet Actors'}

FONT = "Inter, 'Helvetica Neue', Helvetica, Arial, 'Liberation Sans', sans-serif"

# Plot area in canvas pixels.
LEFT = 96
RIGHT = 1000
TOP = 196
BOTTOM = 560


def money(value):
	return f'${value:,.0f}'


def axis_money(value):
	if value == 0:
		return '$0'
	return f'${value / 1000:,.0f}K'


def agents_label(value):
	if value == 0:
		return '0'
	return f'{value / 1000:,.0f}K'


def nice_ceiling(value, step):
	return step * -(-value // step)


def series_by_platform(results):
	series = {'celld': [], 'rivet': []}
	for row in results:
		series[row['platform']].append((row['actors'], row['monthly_usd']))
	for points in series.values():
		points.sort()
	return series


def text(x, y, body, size, fill=INK, weight=400, anchor='start'):
	return (
		f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
		f'fill="{fill}" text-anchor="{anchor}">{body}</text>'
	)


def build_svg(results):
	series = series_by_platform(results)
	x_max = max(agents for agents, _ in series['celld'] + series['rivet'])
	y_max = nice_ceiling(max(cost for _, cost in series['celld'] + series['rivet']), 5000)

	def px(agents):
		return LEFT + agents / x_max * (RIGHT - LEFT)

	def py(cost):
		return BOTTOM - cost / y_max * (BOTTOM - TOP)

	celld_last = series['celld'][-1]
	rivet_last = series['rivet'][-1]
	ratio = celld_last[1] / rivet_last[1]
	agents_at_end = f'{x_max:,}'

	parts = [
		f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
		f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="{FONT}">',
		'<style>@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&amp;display=block");</style>',
		f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAGE}"/>',
	]

	# Headline and method line.
	parts.append(text(LEFT - 40, 74, f'Rivet: {ratio:.1f}× lower storage-request cost than celld', 40, weight=700))
	parts.append(text(
		LEFT - 40, 112,
		f'Monthly object-storage request cost at {agents_at_end} concurrent agents. Same SQLite workload, same machines.',
		20, fill=SECONDARY,
	))

	# Legend row. Identity is also carried by the direct labels at the line ends.
	legend_x = LEFT - 40
	for platform in ['celld', 'rivet']:
		parts.append(
			f'<line x1="{legend_x}" y1="148" x2="{legend_x + 24}" y2="148" '
			f'stroke="{COLORS[platform]}" stroke-width="4" stroke-linecap="round"/>'
		)
		parts.append(text(legend_x + 34, 154, escape(NAMES[platform]), 17, fill=SECONDARY))
		legend_x += 34 + len(NAMES[platform]) * 9 + 40

	# Recessive grid and axes.
	for step in range(0, int(y_max) + 1, 5000):
		y = py(step)
		stroke = AXIS if step == 0 else GRID
		parts.append(f'<line x1="{LEFT}" y1="{y:.1f}" x2="{RIGHT}" y2="{y:.1f}" stroke="{stroke}" stroke-width="1"/>')
		parts.append(text(LEFT - 12, y + 5, axis_money(step), 15, fill=MUTED, anchor='end'))
	for agents in range(0, x_max + 1, 5000):
		parts.append(text(px(agents), BOTTOM + 26, agents_label(agents), 15, fill=MUTED, anchor='middle'))
	parts.append(text((LEFT + RIGHT) / 2, BOTTOM + 50, 'Concurrent agents', 15, fill=MUTED, anchor='middle'))

	# Lines, then point markers with a surface ring, Rivet drawn last so it stays visible at the baseline.
	for platform in ['celld', 'rivet']:
		points = ' '.join(f'{px(a):.1f},{py(c):.1f}' for a, c in series[platform])
		parts.append(
			f'<polyline points="{points}" fill="none" stroke="{COLORS[platform]}" '
			f'stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
		)
		for agents, cost in series[platform]:
			if agents < 2000:
				continue
			parts.append(
				f'<circle cx="{px(agents):.1f}" cy="{py(cost):.1f}" r="5" '
				f'fill="{COLORS[platform]}" stroke="{PAGE}" stroke-width="2"/>'
			)

	# Direct labels at the line ends.
	end_x = px(x_max) + 16
	parts.append(text(end_x, py(celld_last[1]) - 4, 'celld', 17, fill=SECONDARY))
	parts.append(text(end_x, py(celld_last[1]) + 20, f'{money(celld_last[1])}/mo', 20, weight=700))
	parts.append(text(end_x, py(rivet_last[1]) - 26, 'Rivet', 17, fill=SECONDARY))
	parts.append(text(end_x, py(rivet_last[1]) - 2, f'{money(rivet_last[1])}/mo', 20, weight=700))

	# Footnote: how the numbers were produced.
	parts.append(text(
		LEFT - 40, HEIGHT - 22,
		'Each agent is awake 60 s and makes one SQLite write every 10 s. '
		'Measured Azure Blob requests, priced at S3 rates. 6 to 24 nodes.',
		14, fill=MUTED,
	))

	parts.append('</svg>')
	return '\n'.join(parts) + '\n'


def render_png(svg_path, png_path):
	"""Screenshot the SVG in headless Chromium, then crop to the canvas.

	Headless Chromium's viewport is shorter than `--window-size`, so the window is
	made taller than the canvas and the screenshot is cropped back to 2x its size.
	"""
	browser = shutil.which('chromium') or shutil.which('chromium-browser') or shutil.which('google-chrome')
	if browser is None:
		raise SystemExit('--png needs a local Chromium or Chrome on PATH')
	with tempfile.TemporaryDirectory() as work:
		shot = Path(work) / 'shot.png'
		subprocess.run(
			[
				browser, '--headless', '--no-sandbox', '--disable-gpu', '--hide-scrollbars',
				'--force-device-scale-factor=2', f'--window-size={WIDTH},{HEIGHT + 400}',
				'--virtual-time-budget=5000',
				f'--screenshot={shot}', svg_path.resolve().as_uri(),
			],
			check=True,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
		)
		with Image.open(shot) as image:
			image.convert('RGB').crop((0, 0, WIDTH * 2, HEIGHT * 2)).save(png_path, optimize=True)


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('run', help='report folder name, for example scale-25k-20261108')
	parser.add_argument('--png', action='store_true', help='also render social.png at 2x with headless Chromium')
	args = parser.parse_args()

	out = ROOT / 'report' / args.run
	summary = json.loads((out / 'summary.json').read_text())
	svg_path = out / 'social.svg'
	svg_path.write_text(build_svg(summary['results']))
	print(svg_path)

	if args.png:
		png_path = out / 'social.png'
		render_png(svg_path, png_path)
		print(png_path)


if __name__ == '__main__':
	main()
