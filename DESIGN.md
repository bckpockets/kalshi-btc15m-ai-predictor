---
name: BTC / 15
description: Restrained dark research terminal with numeric hierarchy and source clarity.
colors:
  background: "#101519"
  surface: "#1b232a"
  text: "#edf1f3"
  secondary: "#a9bac5"
  eyebrow: "#9caeb8"
  up: "#55d9b0"
  down: "#ff8f92"
  reference: "#e0ba69"
  comparison: "#b8c8d8"
  vwap: "#d9bb77"
  time-marker: "#8193a0"
typography:
  body:
    fontFamily: "sans-serif"
  title:
    fontWeight: 650
    letterSpacing: "-0.04em"
  section:
    fontWeight: 500
  forecast:
    fontFamily: "monospace"
    fontSize: "clamp(36px,5vw,76px)"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.04em"
  eyebrow:
    fontSize: "0.75rem"
    letterSpacing: "0.14em"
  caption:
    fontSize: "0.85rem"
  tab:
    fontSize: "1rem"
spacing:
  page-top: "2rem"
  forecast-vertical: "20px"
components:
  content:
    width: "1500px"
  forecast:
    padding: "20px 0"
    typography: "{typography.forecast}"
---

# BTC / 15 design system

## Overview

Mode: Operate. Restrained dark trading terminal, numeric hierarchy and source clarity. No illustrative assets or fabricated examples in the live UI. Native Streamlit controls retain their framework styles.

## Colors

UP uses the mint accent and DOWN uses rose, always paired with names. The official threshold uses the reference gold. Comparison lines use blue-gray; visible-window VWAP and the current-time marker have distinct source-defined colors. Main charts share the page background. Framework-rendered line charts retain Streamlit styling.

## Typography

Native Streamlit sans-serif supports headings, controls and data labels. Forecast numerals use monospace; native metrics request tabular numerals. Sizes not specified above remain framework defaults.

## Layout

Main order: title, price/countdown/threshold/freshness, complementary forecast pair, research tabs. The content width token is a maximum, not a fixed width. Metrics use four columns in a 1.2:1.2:1:1 ratio and forecasts use two equal columns. The sidebar holds process-level research controls. Streamlit owns narrow-screen stacking; no custom breakpoint is defined.

## Shapes

Forecasts use a two-pixel top rule colored for their side. No custom corner radius or shadow is defined; controls retain Streamlit defaults.

## Components

The two-second fragment refresh updates observations and forecasts. Research tabs cover market, probability path, observed performance, paper portfolio, and rules. Required states include connecting, stale, missing threshold, unsupported rules, final-minute suspension, no active market, no history, no trained model, no quote depth, and no qualifying edge.

Plotly charts use the dark template, 350px default height, margins of 0px left, 12px right, 15px top and 0px bottom, and a horizontal legend above the plot. Series names and threshold annotations supplement color.

## Do's and Don'ts

- Do preserve semantic names alongside UP and DOWN colors.
- Do distinguish unavailable forecasts from numeric zero.
- Don't present proxy estimates as validated model quality.
- Don't claim browser visual approval from source inspection.

Verification: source review and Streamlit AppTest; rendered desktop/mobile screenshot review unavailable because the environment prohibited Chromium socket creation. Responsive composition, keyboard focus and visual contrast remain unverified in a real browser. No screenshot or visual-review approval is claimed.
