# Changelog

The autolearner appends an entry here whenever it applies a tested Opus patch (`balatro/learn.py` `apply`).
Manual changes are recorded here as well.

## 2026-11-02 – 0.3: audit fixes, controls and analysis tools

- Preserve explicit zero/one joker counters; roll random scoring effects during round simulations instead of
  treating expected-score winners as guaranteed. Shop advice and execution share the automatic-mode rules.
- Roll back failed learner patches on test timeouts, interruptions and commit failures; preserve dirty files.
  Use `python -m tests [--core]` for the shared test selection, including HUD and telemetry checks.
- Confirm joker/card drags against fresh telemetry, reorder Death targets safely, and wait for the visible
  Cash Out button. Pause/resume/single-step through `control` or the local lab; one autopilot per user.
- Telemetry v3 adds seed/stake/resources/readiness and permanent card/Pillar history. Re-run the game patcher
  to enable the metadata; old strips remain readable.
- Local `inspect` browser UI: replay/screenshots/error filters, editable state, joker order, hand levels and
  selected-card scoring. `boss` and `deck` forecasts also work from the CLI. Verdant Leaf forecasts and the
  autopilot account for selling a joker to remove its debuff.
- Run version fingerprints/statistics, paired seeded-game comparisons, and isolated Git-revision benchmarks.
  Thirty-day routine-image/learner-work retention keeps logs and failure evidence; `prune` previews cleanup.
- Regression tests cover synthetic drag confirmation, real Cash Out screenshots, controls, rollback failures,
  random score outcomes, HTTP validation, deck/boss planning, statistics, retention and revision comparisons.
  Real game/stream drag calibration and actual paired game runs remain external integration checks.

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
| Button search looks beyond its reach before discarding cut-off blobs (a pack SELECT at the crop edge was lost) | run 20261031-194150 hung in a Buffoon Pack |

Results that day: three wins (ante 8), best Endless run to ante 10.

## 2026-10-31 12:46 – autolearner, run 20261031-115250

Applied automatically (tests green). Files: balatro/optimizer.py, tests/test_optimizer.py- `optimizer.recommend`: when the round isn't won yet, ties between comparable plays are now broken by joker utility → win chance → expected round points → focus hand → score (the focus hand used to come before win chance and expectation; second piece of evidence: runs 101337 move 238, 115250 move 292). Test: `test_expected_score_before_focus_when_not_yet_won`.

## 2026-10-31 13:32 – autolearner, run 20261031-125000

Applied automatically (tests green). Files: balatro/engine.py, balatro/optimizer.py, tests/test_engine.py

- **`balatro/engine.py`:** when the blind is The Hook and cards are held, Green Joker loses 1 mult before scoring (the vanilla discard-context order). This fixes a clear rule bug and the top-priority engine mismatch. Covered by `tests/test_learned.py` (case #392 now matches) and new asserts in `tests/test_engine.py`: 32 × 7 with held cards, 32 × 8 with nothing held.
- **`balatro/optimizer.py` `_after_play`:** after its own Hook discard simulation, it now scores with `boss_disabled=True`. Without this, the engine change above would subtract the Green Joker mult twice in rollouts. Covered by the new `_after_play` assert in `tests/test_engine.py`, which checks the −1 is applied once.
- Ramen goes through the same discard path but has no evidence yet, so I left it out of the engine. It is noted in learnings.md.

## 2026-10-31 20:07 – autolearner, run 20261031-194150

Applied automatically (tests green). Files: balatro/optimizer.py, tests/test_optimizer.py

- **`balatro/optimizer.py`, `_joker_plan` play branch:** under The Arm, if the played hand is above level 1:
  - The simulated state after the play now has that hand one level lower, so the round simulation sees the weaker later hands.
  - The play's joker-value score gets −0.5 with the reason "The Arm: X loses a level". This mirrors the +0.5 that Burnt Joker's level-up already gets, and it is only applied while the boss is active.
  - **Why:** the boss's effect was simply missing from the simulation, which is a clear bug. Comparable options now go to the play that doesn't cost a level. Win chance still comes first, and plays scoring under half of the best one are still filtered out.
  - **Test:** `tests/test_optimizer.py::test_the_arm` checks that Pair 3→2 gives a negative value, High Card at level 1 is unchanged, the original state is untouched, and nothing changes when the boss is disabled.

No other code changes. Crystal Ball and the planet rerolls have one piece of evidence each, so they only went into learnings.md.

## 2026-10-31 – manual, from docs/research/strategies.md

- **`strategy.sell_candidates`:** dropped the blanket rules "flat joker too weak from ante 5" and "economy joker useless from ante 6". The phase weighting stays in `ROLE_BONUS` only; before, a late flat joker got −2 there and another −2 via `sell_candidates` in `_owned_joker_value` (learnings: tier/role beat the real score gain in 4 runs). Whether a swap pays is decided by `shop_plan`'s score check.
- **`shop.shop_plan`:** the sell action now shows the net score change of the whole swap ("+139 % score/hand after the swap"), i.e. after losing the sold joker's share – not the offer's gross gain.
- **Tests:** `test_sell_candidates` (Golden Joker no longer a late sell candidate), `test_full_slots_sell_then_buy` (swap text), new `test_rental_bought_when_it_rescues_a_weak_run`.

## 2026-10-31 20:27 – autolearner, run 20261031-201325

Applied automatically (tests green). Files: balatro/shop.py, tests/test_strategy.py

- **`balatro/shop.py` `_TIERS`: moved DNA from Tier S to Tier C**, with a comment giving the reason.
  - **Why:** the engine gives DNA no effect and the autopilot never triggers it, so Tier S is simply wrong for this bot. An owned S-tier joker that does nothing and can never be sold is the same pattern learnings already records for Blueprint stuck on the far right (run 194631 move 212). From ante 3 on, DNA now becomes a sell candidate, so the shop can swap it out. The +2 synergy with Hologram stays.
  - **Test:** a new assertion in `tests/test_strategy.py::test_sell_candidates` checks that, with Misprint, DNA and Hiker at ante 4, only DNA is flagged.
- I did not change the focus weighting. This run is the second case of a focus taken from planet levels rather than actual play (the first was run 094727 move 137), which would have allowed a change. But any new weights would have been a guess, so it is recorded in learnings with concrete ideas instead.

## 2026-11-01 08:26 – autolearner, run 20261101-081407

Applied automatically (tests green). Files: balatro/models.py, balatro/shop.py, tests/test_strategy.py

- **`balatro/models.py`:** new field `State.upcoming_boss`, filled automatically from the telemetry through `from_dict`.
- **`balatro/shop.py`:** new helper `_need_this_ante`, now used at both places that compute `need_curr`. It uses the current blind if there is one, otherwise the upcoming boss, otherwise a generic 2× boss. This is a clear bug, since The Wall's 4× was known but not used. The same target also feeds the weak/strong buy thresholds and the reroll goals. Without telemetry it behaves as before.
- **Test:** `tests/test_strategy.py::test_swap_checked_against_upcoming_boss` replays the move-99 state and checks that no sale happens when the upcoming boss is The Wall. I first added a temporary assertion confirming that the same state does trigger the sale without the boss info, so the test reproduces the bug. Then I removed that assertion.

## 2026-11-01 10:26 – autolearner, run 20261101-094338

Applied automatically (tests green). Files: balatro/strategy.py, tests/test_strategy.py

- **`balatro/strategy.py`, `joker_synergy`:** Madness now loses 2 points for each owned non-eternal Tier S/A joker, counting at most 2 (two jokers are destroyed per ante). The penalty applies both to Madness as a shop offer and to an owned Madness in `_owned_joker_value`. This is a clear rule gap: a documented, always-firing destroy effect was not valued at all. **Test:** `tests/test_strategy.py::test_madness_costs_the_jokers_it_destroys` rebuilds the move-90 shop and checks the size of the penalty. With the fix temporarily switched off, the test failed and the plan was "sell Erosion for Madness", exactly as in the run.

## 2026-11-01 – manual

- **`autopilot.button_near`:** a blob taller than a button is cut to its longest run of solidly filled rows before
  the size check. Scary Face's SELL button sat on Green Joker's green hat; the merged blob (88 px) was rejected and
  the autopilot hung in the shop (run 20261101-105644 move 128). Test: case 7b in `tests/test_autopilot.py`.

## 2026-11-01 11:35 – autolearner, run 20261101-105644

Applied automatically (tests green). Files: balatro/packs.py, balatro/shop.py, balatro/strategy.py, tests/test_packs.py, tests/test_strategy.py

- **`balatro/packs.py` (`_choose_standard`):** when Hologram is owned, every card scores at least 1, because each added card gives +X0.25. This is a clear bug: the shop buys the pack for this exact reason. Test: `tests/test_packs.py::test_standard_plain_feeds_hologram`.
- **`balatro/shop.py` (`shop_plan`, swap branch):** a swap is now refused when `rep_gain < −5%` and the new joker is neither SCALING nor ECON. Such a joker shows its whole effect in the simulation, so scoring less now never pays off. Photograph at Z212 (−0%) is still allowed. Justified by the second piece of evidence in learnings.md. Test: `tests/test_strategy.py::test_no_swap_for_static_joker_that_scores_less`, rebuilt from the logged Z168 state. With the guard switched off it fails with the same −14% swap, so it does reproduce the bug.
- **`balatro/strategy.py` (`blind_choice`):** skipping the Big Blind is now checked against the known boss (The Wall 4×, Violet Vessel 6×), and the output text names that boss. This fixes a clear bug in both the rule and the text. Test: an extension of `tests/test_strategy.py::test_blind_choice` (1,600 vs. 3,200 at ante 2).

## 2026-11-01 – manual, cross-run analysis of the last 5 runs

| Change | Trigger |
|---|---|
| Reroll for an xMult joker from ante 4 whenever a weak joker (value < 8) could be swapped, not only for tier C/D sell candidates | 201325/081407 lost at A4 with $40–48 and almost no xMult; 112941 A5 $49 with five flat +mult jokers rerolled for planets |
| Planet rerolls only with a clear focus hand | ~7 runs of planet rerolls with ≈0 return (105644 ≈$110 for one Uranus at +0%) |

## 2026-11-01 21:01 – autolearner, run 20261101-203240

Applied automatically (tests green). Files: balatro/shop.py, tests/test_strategy.py

- **`balatro/shop.py` `_owned_joker_value`:** a passive joker (from `engine.PASSIVE`, except Chicot) with under 1% simulated gain and no synergy is now valued as Tier C. Why: the engine simulates a passive joker's whole effect, so +0% means a dead slot. The earlier hypothesis "full slots with A/B jokers that add nothing → no swap" (201325) was the first piece of evidence; this run is the second. **Test:** `tests/test_strategy.py::test_dead_passive_joker_is_swapped_first` replays the exact move-174 state. Before the fix it produced the same reroll and the same ~144,269 estimate as the log; now it plans "sell Oops, buy Photograph". It also checks that Oops keeps its value when Bloodstone is present.
- **`balatro/shop.py` `_min_buy_value`:** a voucher the context rates below its base points is only bought if the money left afterwards stays at or above the interest cap ($25 normally). Why: this is the fourth piece of evidence for the "useless voucher" hypothesis (194150, 105644 and 094338 before), and the logic contradicted itself (rated as a minus, bought anyway). Crystal Ball at $63 is still bought, and the existing test for that still passes. **Test:** `test_downrated_voucher_only_from_idle_money`.

## 2026-11-01 21:27 – autolearner, run 20261101-205435

Applied automatically (tests green). Files: balatro/shop.py, tests/test_strategy.py

- **`balatro/shop.py`, swap branch in `shop_plan`.** If a swap to Vampire loses more than 5%, the bot now re-simulates with Vampire at the xMult the deck can feed it: 1 + 0.1 × the number of enhanced cards. If the swap still loses more than 5%, it doesn't happen.
  - Why: the scaling exemption assumed Vampire would grow, but Vampire only grows on enhanced cards. I treated this as a clear bug. The "% after the swap" text still shows the current effect.
  - Test: `tests/test_strategy.py::test_vampire_needs_enhanced_cards`. It uses the full real state from move 320. I disabled the fix temporarily and confirmed the old code makes exactly the move-320 decision (sell Odd Todd, −35%). With the fix there is no swap. A control case with 26 enhanced cards still makes the swap.

## 2026-11-02 – manual

| Change | Trigger |
|---|---|
| The Ox: the hand comes from telemetry (`most_played_poker_hand`, fixed by the game after each boss) instead of the live play counts. **Re-run `patch_balatro.bat` on the Shadow PC.** | run 212446 Z241: Pair (8×) played with 3 discards left, $43 → $0; the engine thought High Card (9×) was the Ox hand |
| A new run waits for a running autolearner, then restarts on the new code | the restart was skipped while Opus ran, so the patches of runs 203240/205435 were never loaded |
| Joker swap: the sold joker is the one whose replacement scores best (among those the value rule allows), not just the lowest-valued one | run 212446 Z285–295: only Banner (−42%) was tried for Smiley Face/Seltzer/Sock and Buskin, ~$60 rerolled at A7 instead; Half Joker would have given +15–21% |
