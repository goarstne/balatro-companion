# Changelog

The autolearner appends an entry here whenever it applies a tested Opus patch (`balatro/learn.py` `apply`).
Manual changes are recorded here as well.

## Autolearner workflow (since 2026-10-31)

- After every run (game over) `learn` evaluates the run; Claude Opus works on a copy of the project and proposes a
  patch (`runs/<run>/opus.md`, `opus.patch`).
- If the re-check is green, the patch is applied **without asking**: applied in the project, the whole test suite
  re-run, and only on green the patched files plus this entry are committed; on red it is rolled back.
- The autopilot (`live --auto`) restarts itself at the next run start when a new commit exists and no Opus run is
  active, so new code takes effect without a manual restart.

## 2026-10-31 – manual changes and applied patches

| Change | Trigger |
|---|---|
| Vouchers: money above the interest cap buys them from value 2; lost interest weighs less for a permanent voucher | a run bought no voucher (threshold 12) |
| Button search: blobs cut to button size by the search crop are not buttons | Smiley Face art taken for BUY, autopilot stuck |
| Win screen → Endless Mode; lost interest counts less the fewer antes are left (not at all from ante 9) | first win, Cash Out had no effect |
| Opus: ≥3 free joker slots → buy scoring jokers; misclicks are no engine cases; text with 0 discards | run 074053 |
| Opus: sell a joker only if the replacement is really bought afterwards | run 082933, a slot stayed empty for 60 moves |
| Opus: no joker swap that costs points before a tight blind; Celestial bonus only with a real level gain | run 091942, The Club lost |
| Strategy research built in: the autopilot skips blinds (Skip button measured); Hieroglyph/Petroglyph devalued | skip recommended in 4 runs, never clicked |
| Opus: always take a free negative joker; Telescope only if the most played hand is the focus (card.lua checked) | run 094727 |
| Cerulean Bell: discards include the forced card (state_events.lua) | run 101337 hung at the ante-8 boss |
| Keep pairs; joker growth only between comparable hands; discard junk plays; Seed Money steps above $25 count half; Opus: kickers no longer switch Half Joker off | user observations, runs 094727/101337/082933 |
| Spectral packs rated like Arcana packs; Hieroglyph/Petroglyph −5 | 24 Spectral offers in 9 runs, none bought |
| Opus: no joker rerolls when no owned joker is replaceable | run 105755, $45 rerolled away |
| Autolearner applies tested patches automatically, documents them; the autopilot reloads between runs | user decision |
| Project translated to English (widget, advice texts, CLI, docs) | GitHub release |

Results that day: three wins (ante 8), best Endless run to ante 10.

## 2026-10-31 12:46 – autolearner, run 20261031-115250

Applied automatically (tests green). Files: balatro/optimizer.py, tests/test_optimizer.py- `optimizer.recommend`: when the round isn't won yet, ties between comparable plays are now broken by joker utility → win chance → expected round points → focus hand → score (the focus hand used to come before win chance and expectation; second piece of evidence: runs 101337 move 238, 115250 move 292). Test: `test_expected_score_before_focus_when_not_yet_won`.
