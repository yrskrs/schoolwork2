#!/usr/bin/env python3
"""Ergonomics audit for the SchoolNet UI (student + teacher pages).

Complements ``audit_ui_design.py`` (colours/contrast) with layout ergonomics:

* how far a page must be scrolled (screen count) and how dense it is;
* how many interactive elements compete on one screen;
* tap-target sizes (WCAG 2.5.8 asks for >= 24x24 CSS px; 44 px is comfortable);
* overlapping clickable areas (the classic "I clicked the wrong thing");
* actions packed into every card (button soup), repeated identical CTAs;
* typography noise inside the content column (too many sizes, < 12 px text);
* truncation, heading-order jumps, distinct left edges (alignment rhythm);
* number of visually different button styles on one page.

Usage:
    .audit-venv/bin/python scripts/audit_ui_ergonomics.py --out .local-audits/ergonomics
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PAGES: list[tuple[str, str, bool, str]] = [
    # id, path, requires teacher login, role group
    ("feed", "/", False, "student"),
    ("login", "/teacher/login/", False, "public"),
    ("assignment_detail", "/assignment/193/", False, "student"),
    ("assignment_submit", "/assignment/193/submit/", False, "student"),
    ("my_submissions", "/my-submissions/", False, "student"),
    ("submission_view", "/submission/1253/", False, "student"),
    ("teacher_dashboard", "/teacher/", True, "teacher"),
    ("assignment_create", "/teacher/assignment/create/", True, "teacher"),
    ("assignment_edit", "/teacher/assignment/193/edit/", True, "teacher"),
    ("submissions_all", "/teacher/submissions/", True, "teacher"),
    ("assignment_submissions", "/teacher/assignment/193/submissions/", True, "teacher"),
    ("file_viewer", "/teacher/view-file/1253/", True, "teacher"),
    ("gradebook", "/teacher/gradebook/", True, "teacher"),
    ("reports", "/teacher/reports/", True, "teacher"),
    ("students", "/teacher/students/", True, "teacher"),
    ("settings", "/teacher/settings/", True, "teacher"),
    ("profile", "/teacher/profile/", True, "teacher"),
    ("access_links", "/teacher/access-links/", True, "teacher"),
    ("backups", "/teacher/backups/", True, "teacher"),
    ("updates", "/teacher/updates/", True, "teacher"),
    ("activity", "/teacher/activity/", True, "teacher"),
    ("profiles", "/teacher/profiles/", True, "teacher"),
    ("ai_settings", "/teacher/ai-settings/", True, "teacher"),
    ("ai_batch", "/teacher/ai/batch-check/", True, "teacher"),
    ("outside_students", "/teacher/outside-students/", True, "teacher"),
    ("roster_sync", "/teacher/roster-sync/", True, "teacher"),
    ("rescheduled_calendar", "/teacher/rescheduled-calendar/", True, "teacher"),
    ("tailscale", "/teacher/settings/tailscale/", True, "teacher"),
    ("students_import", "/teacher/students/import/", True, "teacher"),
]

AUDIT_JS = r"""
(minViewportHeight) => {
  const out = {};
  const vis = (el) => {
    // In record layout the table head is visually hidden (clip-path), its links
    // are not real tap targets.
    const table = el.closest('table');
    if (table && table.classList.contains('is-record-layout') && el.closest('thead')) return false;
    if (typeof el.checkVisibility === 'function') {
      try {
        if (!el.checkVisibility({opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true})) return false;
      } catch (e) { /* older engines */ }
    } else {
      for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
        const cs = getComputedStyle(n);
        if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
      }
    }
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && r.bottom > -2000;
  };
  const rect = (el) => { const r = el.getBoundingClientRect(); return {x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height}; };
  const label = (el) => (el.getAttribute('aria-label') || el.textContent || el.value || el.getAttribute('title') || '').replace(/\s+/g, ' ').trim().slice(0, 60);
  const norm = (s) => s.toLowerCase().replace(/\s+/g, ' ').replace(/[0-9]+/g, '#').trim();

  const interactive = [...document.querySelectorAll('a[href], button, input:not([type="hidden"]), select, textarea, summary, [role="button"], [tabindex]:not([tabindex="-1"])')]
    .filter(vis);
  const items = interactive.map(el => {
    const r = rect(el);
    const cs = getComputedStyle(el);
    return {tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 50), label: label(el), type: el.type || '',
            x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w), h: Math.round(r.h), fs: parseFloat(cs.fontSize) || 0,
            bg: cs.backgroundColor, color: cs.color, container: !!el.closest('dialog[open], .modal-overlay.active, .notifications-panel'),
            inLabel: !!el.closest('label')};
  });

  // Tap targets
  // A checkbox/radio wrapped in <label> inherits the label as its hit area.
  const hardSmall = items.filter(i => !i.inLabel &&
    ((i.w < 24 || i.h < 24) && i.w > 0 && i.h > 0));
  const softSmall = items.filter(i => (i.h < 40 || i.w < 40) && i.w > 0 && i.h > 0);

  // Overlap of clickable areas (neither contains the other, area > 20% of smaller)
  const overlaps = [];
  for (let i = 0; i < interactive.length; i++) {
    for (let j = i + 1; j < interactive.length; j++) {
      const a = interactive[i], b = interactive[j];
      if (a.contains(b) || b.contains(a)) continue;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const ix = Math.max(0, Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left));
      const iy = Math.max(0, Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top));
      if (!ix || !iy) continue;
      const inter = ix * iy, minArea = Math.min(ra.width * ra.height, rb.width * rb.height);
      if (minArea > 0 && inter / minArea > 0.2) {
        overlaps.push({a: label(a), b: label(b), ratio: Math.round((inter / minArea) * 100) / 100});
      }
      if (overlaps.length > 25) break;
    }
  }

  // Buttons per card-like container
  const cardSel = '.assignment-card, .dashboard-card, .student-sub-card, .submission-row, .form-card';
  const cards = [...document.querySelectorAll(cardSel)].filter(vis);
  let cardStats = {containers: 0, actions: [], maxActions: 0, worst: null};
  for (const card of cards) {
    const outer = card.closest(cardSel);
    if (outer && outer !== card) continue; // nested card: count once, on the outer one
    const acts = [...card.querySelectorAll('a[href], button')].filter(a => vis(a) && a.closest(cardSel) === card);
    if (!acts.length) continue;
    cardStats.containers++;
    cardStats.actions.push(acts.length);
    if (acts.length > cardStats.maxActions) {
      cardStats.maxActions = acts.length;
      cardStats.worst = {label: (card.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 70), actions: acts.length};
    }
  }
  cardStats.avgActions = cardStats.actions.length ? Math.round((cardStats.actions.reduce((a, b) => a + b, 0) / cardStats.actions.length) * 10) / 10 : 0;
  delete cardStats.actions;

  // Repeated CTA labels
  const counts = new Map();
  for (const i of items) {
    const n = norm(i.label);
    if (n.length < 3) continue;
    counts.set(n, (counts.get(n) || 0) + 1);
  }
  const repeated = [...counts.entries()].filter(([, c]) => c >= 3).sort((a, b) => b[1] - a[1]).slice(0, 10)
    .map(([label, count]) => ({label, count}));

  // Typography inside <main>
  const main = document.querySelector('main') || document.body;
  const sizes = new Map();
  const weights = new Map();
  let textChars = 0, smallTextChars = 0, textEls = 0;
  for (const el of main.querySelectorAll('p, li, span, td, th, dd, dt, div, h1, h2, h3, h4, strong, em, label, a, button')) {
    if (!vis(el)) continue;
    const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    if (!own) continue;
    const cs = getComputedStyle(el);
    const fs = Math.round((parseFloat(cs.fontSize) || 0) * 10) / 10;
    sizes.set(fs, (sizes.get(fs) || 0) + 1);
    weights.set(cs.fontWeight, (weights.get(cs.fontWeight) || 0) + 1);
    textChars += own.length; textEls++;
    if (fs < 12) smallTextChars += own.length;
  }

  // Truncation
  const truncated = [];
  for (const el of main.querySelectorAll('*')) {
    if (!vis(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.textOverflow !== 'ellipsis') continue;
    if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0) {
      truncated.push({label: (el.textContent || '').trim().slice(0, 50), cls: (el.className || '').toString().slice(0, 40)});
      if (truncated.length > 20) break;
    }
  }

  // Headings order
  const headings = [...main.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(vis).map(h => ({level: +h.tagName[1], text: (h.textContent || '').trim().slice(0, 50)}));
  let headingJumps = 0;
  for (let i = 1; i < headings.length; i++) if (headings[i].level - headings[i - 1].level > 1) headingJumps++;

  // Alignment rhythm: distinct left edges of block children in the main column
  const edges = new Map();
  for (const el of main.querySelectorAll('section, article, .card, .assignment-card, .form-group, .page-hero, .empty-state')) {
    if (!vis(el)) continue;
    const l = Math.round((el.getBoundingClientRect().left + scrollX) / 4) * 4;
    edges.set(l, (edges.get(l) || 0) + 1);
  }
  const edgeList = [...edges.entries()].filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1]).slice(0, 8);

  // Button visual variants
  const btnStyles = new Set();
  for (const b of document.querySelectorAll('a.btn, button.btn, .btn')) {
    if (!vis(b)) continue;
    const cs = getComputedStyle(b);
    btnStyles.add(cs.backgroundColor + '|' + cs.color + '|' + cs.borderColor);
  }

  const docH = document.documentElement.scrollHeight;
  const first = items.filter(i => i.y < minViewportHeight && !i.container);
  const primaryFirst = first.filter(i => /(^| )(btn-primary|btn-success|btn-danger|btn-warning)( |$)/.test(i.cls));

  return {
    docHeight: docH,
    screens: Math.round((docH / minViewportHeight) * 10) / 10,
    interactive: items.length,
    interactiveFirstScreen: first.length,
    primaryFirstScreen: primaryFirst.length,
    hardSmallCount: hardSmall.length,
    hardSmall: hardSmall.slice(0, 12),
    softSmallCount: softSmall.length,
    softSmallSample: softSmall.slice(0, 10).map(i => ({tag: i.tag, cls: i.cls, label: i.label, w: i.w, h: i.h})),
    overlaps,
    cards: cardStats,
    repeated,
    typography: {
      sizes: [...sizes.entries()].sort((a, b) => a[0] - b[0]),
      weights: [...weights.entries()].sort((a, b) => b[1] - a[1]),
      distinctSizes: sizes.size,
      smallTextShare: textChars ? Math.round((smallTextChars / textChars) * 100) : 0,
      charsPerKpx: docH ? Math.round(textChars / (docH / 1000)) : 0,
      textEls,
    },
    truncatedCount: truncated.length,
    truncated: truncated.slice(0, 8),
    headings: {count: headings.length, jumps: headingJumps, first: headings.slice(0, 4)},
    alignment: {edges: edgeList, distinct: edges.size},
    buttonVariants: btnStyles.size,
    emojiCount: (main.innerText.match(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu) || []).length,
    badges: main.querySelectorAll('.badge, .status-badge, .class-prominent-badge, .due-badge, .publish-date-badge, .subject-tag, .class-tag').length,
    firstContentTop: (() => {
      const sel = '.dashboard-card, .assignment-card, .student-sub-card, tbody tr, .form-card, .log-entry, .activity-row';
      const els = [...document.querySelectorAll(sel)].filter(e => { const q = e.getBoundingClientRect(); return q.height > 20; });
      if (!els.length) return null;
      return Math.round(Math.min(...els.map(e => e.getBoundingClientRect().top + scrollY)));
    })(),
  };
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--out", default=".local-audits/ergonomics")
    parser.add_argument("--chrome", default="/usr/bin/chromium")
    parser.add_argument("--user", default="teacher_lab")
    parser.add_argument("--password", default=None)
    parser.add_argument("--label", default="")
    parser.add_argument("--only", default="")
    parser.add_argument("--max-screens-mobile", type=float, default=0,
                        help="Fail (exit 1) if a mobile page needs more screens than this.")
    parser.add_argument("--max-small", type=int, default=0,
                        help="Fail if a desktop page has more tap targets smaller than 24 px.")
    parser.add_argument("--max-repeated", type=int, default=0,
                        help="Fail if one CTA label repeats more often than this on a page.")
    parser.add_argument("--max-first-content-top", type=int, default=0,
                        help="Fail if the first record starts lower than this on a phone.")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright не встановлено. Див. docstring.", file=sys.stderr)
        return 2

    password = args.password
    if not password:
        for line in Path(".env.ai-lab.local").read_text().splitlines():
            if line.startswith("LAB_TEACHER_PASSWORD="):
                password = line.split("=", 1)[1].strip()

    out_dir = Path(args.out)
    (out_dir / "screens").mkdir(parents=True, exist_ok=True)
    only = {p.strip() for p in args.only.split(",") if p.strip()}
    suffix = f"-{args.label}" if args.label else ""
    pages = [p for p in PAGES if not only or p[0] in only]
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "base_url": args.base_url, "pages": []}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        for viewport, vp_label in (((1440, 900), "desktop"), ((390, 844), "mobile")):
            for page_id, path, auth, group in pages:
                context = browser.new_context(base_url=args.base_url, viewport={"width": viewport[0], "height": viewport[1]})
                context.add_init_script(
                    "localStorage.setItem('sn-theme','light');"
                    "localStorage.setItem('sn-simplified-site','off');"
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                data = {"page": page_id, "path": path, "group": group, "viewport": vp_label}
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
                    data["status"] = response.status if response else None
                    page.wait_for_timeout(500)
                    if viewport[0] < 500:
                        page.evaluate("() => window.scrollTo(0, 0)")
                    data.update(page.evaluate(AUDIT_JS, viewport[1]))
                    page.screenshot(path=str(out_dir / "screens" / f"{page_id}-{vp_label}{suffix}.png"),
                                    full_page=(vp_label == "desktop"), animations="disabled")
                except Exception as exc:  # noqa: BLE001
                    data["error"] = str(exc)
                data["jsErrors"] = errors[:3]
                report["pages"].append(data)
                print(f"{vp_label:<7} {page_id:<24} status={data.get('status')} screens={data.get('screens')} "
                      f"inter={data.get('interactive')} small={data.get('hardSmallCount')} "
                      f"overlaps={len(data.get('overlaps') or [])} cards={data.get('cards', {}).get('maxActions')}")
                context.close()
        browser.close()

    path = out_dir / f"ergonomics{suffix}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\nЗвіт: {path}")

    failures = []
    for page in report["pages"]:
        name = f"{page['page']} ({page['viewport']})"
        if args.max_screens_mobile and page["viewport"] == "mobile" and (page.get("screens") or 0) > args.max_screens_mobile:
            failures.append(f"{name}: {page['screens']} екранів > {args.max_screens_mobile}")
        if args.max_small and page["viewport"] == "desktop" and (page.get("hardSmallCount") or 0) > args.max_small:
            failures.append(f"{name}: {page['hardSmallCount']} цілей < 24 px > {args.max_small}")
        repeated = (page.get("repeated") or [{}])[0].get("count", 0)
        if args.max_repeated and repeated > args.max_repeated:
            failures.append(f"{name}: «{page['repeated'][0]['label']}» повторюється {repeated} разів")
        if (args.max_first_content_top and page["viewport"] == "mobile"
                and page.get("firstContentTop") is not None
                and page["firstContentTop"] > args.max_first_content_top):
            failures.append(f"{name}: перший запис на {page['firstContentTop']} px")

    if failures:
        print("\nПороги ергономіки перевищено:")
        for line in failures:
            print(" -", line)
        return 1
    if any((args.max_screens_mobile, args.max_small, args.max_repeated, args.max_first_content_top)):
        print("Пороги ергономіки дотримано.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
