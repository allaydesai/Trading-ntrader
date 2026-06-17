---
name: NTrader
description: A quant's workbench — dark, flat, numbers-first backtesting UI
colors:
  terminal-black: "#020617"
  panel: "#0f172a"
  panel-raised: "#1e293b"
  hairline: "#334155"
  hairline-strong: "#475569"
  ink: "#f1f5f9"
  ink-secondary: "#cbd5e1"
  ink-muted: "#94a3b8"
  ink-faint: "#64748b"
  action-blue: "#2563eb"
  action-blue-deep: "#1d4ed8"
  focus-blue: "#3b82f6"
  profit-green: "#4ade80"
  loss-red: "#f87171"
  badge-green-bg: "#14532d"
  badge-red-bg: "#7f1d1d"
  badge-amber-bg: "#713f12"
typography:
  display:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.3
  title:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.05em"
  data:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: "0.25rem"
  md: "0.375rem"
  lg: "0.5rem"
spacing:
  xs: "0.25rem"
  sm: "0.5rem"
  md: "1rem"
  lg: "1.5rem"
components:
  button-primary:
    backgroundColor: "{colors.action-blue}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "0.5rem 1rem"
  button-primary-hover:
    backgroundColor: "{colors.action-blue-deep}"
  card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.lg}"
    padding: "1.5rem"
  input:
    backgroundColor: "{colors.panel-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0.5rem 0.75rem"
  badge-success:
    backgroundColor: "{colors.badge-green-bg}"
    textColor: "{colors.profit-green}"
    rounded: "{rounded.sm}"
    padding: "0.125rem 0.5rem"
  badge-failure:
    backgroundColor: "{colors.badge-red-bg}"
    textColor: "{colors.loss-red}"
    rounded: "{rounded.sm}"
    padding: "0.125rem 0.5rem"
  nav-link-active:
    backgroundColor: "{colors.panel-raised}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "0.5rem 0.75rem"
---

# Design System: NTrader

## 1. Overview

**Creative North Star: "The Quant's Workbench"**

NTrader's interface is a precision instrument bench: every number in its place, tools at hand, zero ornament. The user is a quant in a long desk session, moving between data import, backtest configuration, and results analysis — the UI's only job is to make those numbers faster to read and easier to trust. The system is dark by physical reality (evening desk work, charts glowing against a dim room), not dark for fashion. Density is welcome; noise is not. A screen full of metrics must feel organized, never loud.

The system explicitly rejects two failure modes named in PRODUCT.md: the **crypto-bro trading app** (neon green/red glow, gamified hype, dark-mode-for-coolness) and the **cookie-cutter SaaS admin template** (identical stat-card grids, decorative icons everywhere, gradient accent abuse). Every visual decision is judged by one question: does it make the data faster to read?

**Key Characteristics:**
- Near-black slate canvas with strictly tonal depth — no shadows, no glass, no gradients
- One action color (blue) reserved for buttons, links, selection, and focus
- Red and green are semantic only: loss and profit, error and success
- Monospace tabular numerals for all market and performance data
- Dense, small-type layouts organized by hairline borders and spacing rhythm

## 2. Colors

A restrained slate ramp carries the whole surface; blue marks action; red and green are reserved for meaning.

### Primary
- **Action Blue** (#2563eb): The single accent. Primary buttons, links, selected nav states, and active controls. Hover deepens to **Action Blue Deep** (#1d4ed8); focus rings use **Focus Blue** (#3b82f6) at 2px. If blue appears somewhere the user cannot click or hasn't selected, it is wrong.

### Neutral
- **Terminal Black** (#020617): The body background everywhere. Nothing sits directly on it except panels and page titles.
- **Panel** (#0f172a): Cards, the nav bar, table alt-rows — the default working surface, one tonal step up.
- **Panel Raised** (#1e293b): Hover states, inputs, active nav pills — the second tonal step. This is the ceiling; there is no third raise.
- **Hairline** (#334155) / **Hairline Strong** (#475569): 1px borders that do the structural work shadows would do elsewhere. Hairline for panel edges and dividers; Hairline Strong for form-control outlines.
- **Ink** (#f1f5f9) → **Ink Secondary** (#cbd5e1) → **Ink Muted** (#94a3b8) → **Ink Faint** (#64748b): The four-step text hierarchy: values, supporting text, labels/secondary, and de-emphasized metadata. Ink Faint fails AA for body copy on Terminal Black — see the Don'ts.

### Semantic (profit / loss / status)
- **Profit Green** (#4ade80): Positive P&L values and success states; paired with **Badge Green** (#14532d) backgrounds for status chips.
- **Loss Red** (#f87171): Negative P&L and errors; paired with **Badge Red** (#7f1d1d).
- **Badge Amber** (#713f12 bg): Pending/warning status only.

### Named Rules
**The Semantic Color Rule.** Red and green encode profit/loss and success/failure — nothing else, ever. They never decorate, never glow, and always travel with a sign or label so hue is not the only carrier.
**The One Accent Rule.** Blue means "you can act here." It covers well under 10% of any screen; its rarity is what makes actions findable.

## 3. Typography

**UI Font:** System sans stack (ui-sans-serif, system-ui, Segoe UI, Roboto)
**Data Font:** System mono stack (ui-monospace, SFMono-Regular, Menlo, Consolas)

**Character:** One quiet, native sans for all chrome; monospace for anything the user reads as a number. The pairing is deliberate workbench pragmatism — the typography should disappear into the data.

### Hierarchy
- **Display** (700, 1.5rem, 1.3): Page titles only — one per page.
- **Title** (600, 1.125rem, 1.4): Panel and card headings.
- **Body** (400, 0.875rem, 1.5): The default UI size. Most of the interface lives here; prose tops out at 65–75ch.
- **Label** (500, 0.75rem, 0.05em tracking, UPPERCASE): Table column headers and metric labels. This is the system's one sanctioned uppercase treatment.
- **Data** (mono, 400, 0.875rem): Prices, P&L, ratios, timestamps, config values. Right-aligned in tables with decimals lining up.

### Named Rules
**The Mono Numbers Rule.** Any value a quant would compare — currency, percentage, ratio, count, timestamp — is set in the mono stack, right-aligned, with consistent decimal precision per column. Sans-serif numbers in a data table are a defect.

## 4. Elevation

This system is **flat by doctrine**. Depth is conveyed entirely by tonal layering (Terminal Black → Panel → Panel Raised) and 1px hairline borders; there are no box-shadows anywhere, including overlays. A dropdown or modal earns separation through a Panel Raised surface, a Hairline Strong border, and a dimmed backdrop — never a shadow or a blur.

### Named Rules
**The Flat Rule.** Two tonal steps above the canvas is the ceiling. If an element needs more separation than Panel Raised plus a border can give, the layout is wrong — restructure instead of reaching for shadows, glows, or glassmorphism.

## 5. Components

Refined and restrained: quiet surfaces, precise 1px borders, small radii, color only where action lives. The same control looks the same on every screen.

### Buttons
- **Shape:** Gently rounded (0.375rem)
- **Primary:** Action Blue (#2563eb) with white text, 0.5rem × 1rem padding, font-medium. Hover deepens to #1d4ed8; disabled drops to #1e40af with `cursor-not-allowed`.
- **Hover / Focus:** Color transitions at ~150ms; focus is a 2px Focus Blue ring offset against the page background — never removed.
- **Secondary:** Panel Raised background, Ink Secondary text, Hairline Strong border; hover raises text to white.

### Cards / Containers
- **Corner Style:** 0.5rem radius
- **Background:** Panel (#0f172a)
- **Border:** 1px Hairline (#334155) — every card has one; borders, not shadows, define edges
- **Internal Padding:** 1rem compact / 1.5rem standard

### Inputs / Fields
- **Style:** Panel Raised (#1e293b) fill, 1px Hairline Strong (#475569) border, 0.375rem radius, Ink text, 0.5rem × 0.75rem padding
- **Focus:** 2px Focus Blue ring, border invisible beneath it
- **Error:** Border and helper text shift to Loss Red; the message states what to do, not just what failed

### Tables
- **Headers:** Label type (0.75rem uppercase, 0.05em tracking, Ink Muted), left-aligned text / right-aligned numerics
- **Rows:** Hairline (#1e293b-level) row dividers; hover fills Panel Raised; alternate rows may tint to Panel
- **Numerics:** Data font, right-aligned; P&L cells in Profit Green / Loss Red with explicit +/− signs

### Status Badges
- **Style:** Dark semantic fill (Badge Green/Red/Amber) with the matching bright text tone, 0.25rem radius, 0.125rem × 0.5rem padding, Label-size text

### Navigation
- **Style:** Panel bar with a 1px Hairline bottom border; links are Body-size font-medium pills. Inactive: Ink Secondary, hover Panel Raised + white. Active: Panel Raised + white. Mobile collapses behind a standard menu toggle.

### Charts (signature)
TradingView Lightweight Charts on Terminal Black canvases. Series colors come from the semantic palette (Profit Green / Loss Red for P&L, Action Blue for equity lines); gridlines at Hairline strength. Chart chrome must look native to the page — no white-themed chart islands.

## 6. Do's and Don'ts

### Do:
- **Do** convey depth with tonal steps (#020617 → #0f172a → #1e293b) and 1px borders — The Flat Rule.
- **Do** set every comparable number in the mono stack, right-aligned, with fixed decimal precision per column.
- **Do** pair every red/green value with a sign, label, or icon so hue is never the only encoding (WCAG AA, color-blind safety).
- **Do** give every interactive element a visible 2px Focus Blue ring and a designed disabled state.
- **Do** design honest async states: skeleton panels while loading, empty states that teach ("No backtests yet — run one from the Run Backtest page"), explicit error panels.
- **Do** keep Ink Muted (#94a3b8) as the floor for body-size text on Terminal Black.

### Don't:
- **Don't** ship anything from the **crypto-bro trading app** family: neon glows, green/red as decoration, gradient hype, gamified streaks (PRODUCT.md anti-reference, verbatim).
- **Don't** build **cookie-cutter SaaS admin** scaffolding: identical icon+number stat-card grids, decorative icons on every label, gradient accents (PRODUCT.md anti-reference).
- **Don't** use box-shadows, glassmorphism, backdrop blurs, or gradient text — flat tonal layering only.
- **Don't** use Ink Faint (#64748b) for body-size copy on Terminal Black — it fails AA (~4.1:1). It is for large text and true metadata only.
- **Don't** put Action Blue on non-interactive elements, and don't introduce a second accent hue.
- **Don't** use colored side-stripe borders (`border-left` > 1px) on cards, alerts, or list items; use a full hairline border plus a semantic badge instead.
- **Don't** animate beyond ~150–250ms state transitions; no entrance choreography, and every transition honors `prefers-reduced-motion`.
