# SupportPilot AI pitch decks

## Business pitch

`SupportPilot-AI-Pitch-Deck.pptx` (editable) and `SupportPilot-AI-Pitch-Deck.pdf` (same content, opens in the browser) — an 11-slide business-audience pitch (problem, why existing bots fail,
capabilities, how it works, the fictional hospital demo, security, Peter-vs-Mary privacy example, human handoff,
target users, pilot plan). Speaker notes are on each slide; slides 6 and 8 carry a 60-second live-demo script
(start the app with `LLM_PROVIDER=demo`, see the main README). The demo uses the keyword stand-in model and a
fictional hospital, and the notes say so.

Claims in the deck are taken from the repository docs. The "~30,000 conversations a day" figure is the documented
design point in `docs/SCALING.md`, not a measured customer result, and the deck states that production go-live is
gated by the readiness evaluation in `docs/EVALUATION.md`.

## Technical deep dive

`SupportPilot-AI-Technical-Deck.pdf` and `SupportPilot-AI-Technical-Deck.pptx` — 15 slides for an engineering
audience or a demo video: the stack, the architecture, one customer turn traced end to end, the live demo, grounding
(BM25 with confidence bands), identity (three JWT audiences), authorization in code, human handoff, resilience,
evaluation gates, the load-test bug, known gaps, and the production target design. The PowerPoint holds each slide as
an image with the full narration script in the speaker notes; the PDF is the same slides.

Numbers on the slides were re-measured for this deck (153 tests passing, 94 % coverage, the fast release gates) or
come from `docs/SCALING.md` §9 and `docs/EVALUATION.md`. The load-test figures are one process on SQLite with a
simulated model, and the slide says so.

A recorded walkthrough of the product is in [`../demo/`](../demo/README.md).
