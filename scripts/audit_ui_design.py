#!/usr/bin/env python3
"""Audit the visual design system of the running SchoolNet site.

The script opens the site in a headless Chromium (Playwright), visits the main
pages in light and dark theme and collects objective design metrics:

* palette entropy (how many distinct colours / radii / fonts are really used),
* hardcoded colours in inline styles and in the loaded stylesheets,
* WCAG contrast failures for text,
* "light leak" surfaces that stay light in the dark theme,
* form control theming,
* CSS/JS weight and stylesheet count.

It writes ``report.json`` plus screenshots into the output directory so the
before/after state of a UI optimisation can be compared.

Install the tooling once (Playwright, system Chromium is reused):
    UV_CACHE_DIR=.uv-cache uv venv .audit-venv
    UV_CACHE_DIR=.uv-cache uv pip install --python .audit-venv/bin/python playwright

Usage:
    .audit-venv/bin/python scripts/audit_ui_design.py \
        --base-url http://127.0.0.1:8010 --out .local-audits/ui-audit

Credentials default to the local AI lab (.env.ai-lab.local, teacher_lab).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

PAGES = [
    ("public_index", "/", False),
    ("login", "/teacher/login/", False),
    ("teacher_dashboard", "/teacher/", True),
    ("assignment_create", "/teacher/assignment/create/", True),
    ("submissions", "/teacher/submissions/", True),
    ("gradebook", "/teacher/gradebook/", True),
    ("reports", "/teacher/reports/", True),
    ("students", "/teacher/students/", True),
    ("settings", "/teacher/settings/", True),
    ("access_links", "/teacher/access-links/", True),
    ("profile", "/teacher/profile/", True),
    ("backups", "/teacher/backups/", True),
    ("student_portal", "/my-submissions/", False),
]

AUDIT_JS = r"""
() => {
  const isDark = document.documentElement.dataset.theme === 'dark';
  const root = getComputedStyle(document.documentElement);
  const tokenNames = [];
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    if (!rules) continue;
    for (const rule of rules) {
      if (rule.style && rule.selectorText && rule.selectorText.includes(':root')) {
        for (const name of rule.style) if (name.startsWith('--')) tokenNames.push(name);
      }
    }
  }

  const parse = (c) => {
    if (!c) return null;
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    if (p.length < 3 || p.some(Number.isNaN)) return null;
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };
  const toHex = (c) => '#' + [c.r, c.g, c.b].map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  };
  const blend = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  const bodyBg = parse(getComputedStyle(document.body).backgroundColor);
  const htmlBg = parse(getComputedStyle(document.documentElement).backgroundColor);
  const fallbackBg = (bodyBg && bodyBg.a > 0) ? bodyBg : (htmlBg || {r: 255, g: 255, b: 255, a: 1});

  const isVisible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0.5 && r.height > 0.5;
  };

  const bgCache = new WeakMap();
  const effectiveBg = (el) => {
    if (bgCache.has(el)) return bgCache.get(el);
    const chain = [];
    let node = el;
    let result = null;
    while (node && node.nodeType === 1) {
      const cs = getComputedStyle(node);
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) { chain.push(c); if (c.a >= 0.999) break; }
      node = node.parentElement;
    }
    result = chain.length ? chain.reduceRight((acc, c) => blend(c, acc), fallbackBg) : fallbackBg;
    bgCache.set(el, result);
    return result;
  };

  const colorCount = new Map();
  const bgCount = new Map();
  const radiusCount = new Map();
  const fontCount = new Map();
  const fontSizes = new Map();
  const shadowCount = new Map();
  const contrastIssues = [];
  const lightLeaks = [];
  const controlIssues = [];
  const hardcodedInline = [];
  let textNodes = 0;
  let visibleEls = 0;

  const bump = (map, key) => { if (key) map.set(key, (map.get(key) || 0) + 1); };

  const all = document.querySelectorAll('body *');
  for (const el of all) {
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE' || el.tagName === 'LINK' || el.tagName === 'META') continue;
    if (!isVisible(el)) continue;
    visibleEls++;
    const cs = getComputedStyle(el);
    bump(colorCount, cs.color);
    const bg = parse(cs.backgroundColor);
    if (bg && bg.a > 0) bump(bgCount, toHex(bg));
    if (cs.borderTopLeftRadius && cs.borderTopLeftRadius !== '0px') bump(radiusCount, cs.borderTopLeftRadius);
    if (cs.fontFamily) bump(fontCount, cs.fontFamily.split(',')[0].replace(/["']/g, '').trim());
    bump(fontSizes, cs.fontSize);
    if (cs.boxShadow && cs.boxShadow !== 'none') bump(shadowCount, cs.boxShadow.slice(0, 40));

    if (el.hasAttribute('style')) {
      const style = el.getAttribute('style') || '';
      if (/#[0-9a-fA-F]{3,8}\b|rgba?\(/.test(style) && !style.includes('var(--')) {
        hardcodedInline.push({tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 60), style: style.slice(0, 120)});
      }
    }

    // Contrast for elements that own text directly. Elements painted on a
    // gradient/photo background cannot be judged from background-color alone.
    const gradientAncestor = (node) => {
      while (node && node.nodeType === 1) {
        const cs2 = getComputedStyle(node);
        if (cs2.backgroundImage && cs2.backgroundImage !== 'none') return true;
        node = node.parentElement;
      }
      return false;
    };
    const ownText = [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim()).map(n => n.textContent.trim()).join(' ').trim();
    if (ownText && !gradientAncestor(el)) {
      textNodes++;
      const fg = parse(cs.color);
      const b = effectiveBg(el);
      if (fg && b && fg.a > 0.05) {
        const fgBlended = blend(fg, b);
        const cr = ratio(fgBlended, b);
        const size = parseFloat(cs.fontSize) || 16;
        const bold = parseInt(cs.fontWeight, 10) >= 700;
        const large = size >= 24 || (bold && size >= 18.66);
        const need = large ? 3 : 4.5;
        if (cr < need) {
          contrastIssues.push({
            tag: el.tagName.toLowerCase(),
            cls: (el.className || '').toString().slice(0, 70),
            text: ownText.slice(0, 60),
            ratio: Math.round(cr * 100) / 100,
            need,
            fg: toHex(fgBlended),
            bg: toHex(b),
          });
        }
      }
    }

    // Light surfaces that should have been re-themed in dark mode.
    const r = el.getBoundingClientRect();
    const area = r.width * r.height;
    if (isDark && bg && bg.a >= 0.9 && area > 4000) {
      const l = lum(bg);
      if (l > 0.62 && !el.closest('img, video, canvas, svg, iframe, pre, code')) {
        lightLeaks.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.className || '').toString().slice(0, 70),
          bg: toHex(bg),
          area: Math.round(area),
          text: (el.textContent || '').trim().slice(0, 40),
        });
      }
    }

    // Form controls must follow the theme.
    if (['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(el.tagName) &&
        el.type !== 'checkbox' && el.type !== 'radio' && el.type !== 'range') {
      const bgc = parse(cs.backgroundColor);
      const fgc = parse(cs.color);
      if (bgc && fgc) {
        const cr = ratio(blend(fgc, bgc), bgc);
        if (cr < 3 || (isDark && lum(bgc) > 0.7)) {
          controlIssues.push({
            tag: el.tagName.toLowerCase(),
            type: el.type || '',
            name: el.name || el.id || '',
            bg: toHex(bgc),
            fg: toHex(fgc),
            ratio: Math.round(cr * 100) / 100,
          });
        }
      }
    }
  }

  // Stylesheet statistics.
  let cssRules = 0, cssBytes = 0, sheets = 0, literalColorDecls = 0, varDecls = 0;
  const selectors = new Map();
  for (const sheet of document.styleSheets) {
    sheets++;
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    if (!rules) continue;
    const walk = (list) => {
      for (const rule of list) {
        if (rule.cssRules) walk(rule.cssRules);
        if (!rule.style) continue;
        cssRules++;
        for (const prop of rule.style) {
          const v = rule.style.getPropertyValue(prop);
          if (/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/.test(v)) {
            if (v.includes('var(--')) varDecls++; else literalColorDecls++;
          }
        }
        if (rule.selectorText) {
          const s = rule.selectorText.trim();
          selectors.set(s, (selectors.get(s) || 0) + 1);
        }
      }
    };
    walk(rules);
  }
  const duplicatedSelectors = [...selectors.entries()].filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]).slice(0, 15);

  const resources = performance.getEntriesByType('resource');
  const byType = (t) => resources.filter(r => r.initiatorType === t);
  const sum = (arr, f) => arr.reduce((a, r) => a + (r[f] || 0), 0);
  const paint = performance.getEntriesByType('paint');
  const fcp = paint.find(p => p.name === 'first-contentful-paint');

  return {
    theme: isDark ? 'dark' : 'light',
    url: location.pathname,
    domNodes: document.querySelectorAll('*').length,
    visibleElements: visibleEls,
    textNodes,
    inlineStyleAttrs: document.querySelectorAll('[style]').length,
    hardcodedInlineSamples: hardcodedInline.slice(0, 12),
    hardcodedInlineCount: hardcodedInline.length,
    sheetCount: sheets,
    cssRuleCount: cssRules,
    literalColorDeclarations: literalColorDecls,
    varColorDeclarations: varDecls,
    duplicatedSelectors,
    palette: {
      textColors: [...colorCount.entries()].sort((a, b) => b[1] - a[1]).slice(0, 24),
      bgColors: [...bgCount.entries()].sort((a, b) => b[1] - a[1]).slice(0, 24),
      textColorCount: colorCount.size,
      bgColorCount: bgCount.size,
      radii: [...radiusCount.entries()].sort((a, b) => b[1] - a[1]),
      fonts: [...fontCount.entries()].sort((a, b) => b[1] - a[1]),
      fontSizes: [...fontSizes.entries()].sort((a, b) => parseFloat(a[0]) - parseFloat(b[0])),
      shadows: [...shadowCount.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8),
    },
    tokens: [...new Set(tokenNames)].sort(),
    contrast: {
      failures: contrastIssues.length,
      samples: contrastIssues.sort((a, b) => a.ratio - b.ratio).slice(0, 15),
    },
    darkMode: {
      lightLeaks: lightLeaks.length,
      samples: lightLeaks.sort((a, b) => b.area - a.area).slice(0, 15),
    },
    controls: {
      issues: controlIssues.length,
      samples: controlIssues.slice(0, 12),
    },
    perf: {
      resourceCount: resources.length,
      cssRequests: byType('link').length,
      scriptRequests: byType('script').length,
      cssBytes: sum(byType('link'), 'encodedBodySize'),
      scriptBytes: sum(byType('script'), 'encodedBodySize'),
      totalBytes: sum(resources, 'encodedBodySize'),
      cssDecodedBytes: sum(byType('link'), 'decodedBodySize'),
      fcpMs: fcp ? Math.round(fcp.startTime) : null,
      domContentLoadedMs: Math.round(performance.getEntriesByType('navigation')[0]?.domContentLoadedEventEnd || 0),
    },
  };
}
"""


def load_lab_password() -> str:
    env_file = Path(".env.ai-lab.local")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("LAB_TEACHER_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("LAB_TEACHER_PASSWORD", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--out", default=".local-audits/ui-audit")
    parser.add_argument("--chrome", default="/usr/bin/chromium")
    parser.add_argument("--user", default="teacher_lab")
    parser.add_argument("--password", default=None)
    parser.add_argument("--label", default="", help="Suffix for the output files (e.g. before/after).")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--only", default="", help="Comma separated page ids to audit.")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright не встановлено. Див. docstring скрипта.", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = out_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    password = args.password or load_lab_password()
    only = {p.strip() for p in args.only.split(",") if p.strip()}
    suffix = f"-{args.label}" if args.label else ""

    pages = [p for p in PAGES if not only or p[0] in only]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base_url": args.base_url,
        "viewport": [args.width, args.height],
        "label": args.label,
        "pages": [],
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        for page_id, path, auth in pages:
            for theme in ("light", "dark"):
                context = browser.new_context(
                    base_url=args.base_url,
                    viewport={"width": args.width, "height": args.height},
                    device_scale_factor=1,
                )
                context.add_init_script(
                    "if (!localStorage.getItem('sn-theme')) localStorage.setItem('sn-theme', '%s');"
                    "if (!localStorage.getItem('sn-simplified-site')) localStorage.setItem('sn-simplified-site', 'off');"
                    % theme
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                status = None
                try:
                    if auth:
                        login = context.new_page()
                        login.goto("/teacher/login/", wait_until="domcontentloaded")
                        login.fill("input[name='username']", args.user)
                        login.fill("input[name='password']", password)
                        login.click("button[type='submit']")
                        login.wait_for_load_state("domcontentloaded")
                        login.close()
                    response = page.goto(path, wait_until="networkidle", timeout=45000)
                    status = response.status if response else None
                    page.wait_for_timeout(600)
                    page.evaluate(
                        "(t) => { document.documentElement.dataset.theme = t;"
                        "document.documentElement.style.backgroundColor = t === 'dark' ? '#0f172a' : '#f8fafc';"
                        "document.documentElement.style.colorScheme = t; }",
                        theme,
                    )
                    page.wait_for_timeout(350)
                    data = page.evaluate(AUDIT_JS)
                except Exception as exc:  # noqa: BLE001 - audit must continue
                    data = {"error": str(exc)}
                data.update({"page": page_id, "path": path, "status": status, "theme": theme, "jsErrors": errors[:5]})
                name = f"{page_id}-{theme}{suffix}.png"
                try:
                    page.screenshot(path=str(shots / name), full_page=True, animations="disabled")
                    data["screenshot"] = str(shots / name)
                except Exception as exc:  # noqa: BLE001
                    data["screenshot_error"] = str(exc)
                report["pages"].append(data)
                print(f"{page_id:<20} {theme:<5} status={status} nodes={data.get('domNodes')} "
                      f"contrast={data.get('contrast', {}).get('failures')} "
                      f"leaks={data.get('darkMode', {}).get('lightLeaks')}")
                context.close()
        browser.close()

    report_path = out_dir / f"report{suffix}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nЗвіт: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
