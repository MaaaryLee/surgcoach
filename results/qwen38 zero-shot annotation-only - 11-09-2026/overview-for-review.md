# Qwen3.8 zero-shot on annotation-only QA: overview

**Setup.**
- Model: Qwen3.8-27B (Q4_K_M), run text-only.
  - It answers from the dataset annotations alone and sees no video.
  - The same model, prompted with Eric's v5 rubric (1–5 per check, where 3 or below is a defect), then reviews every answer.
- Zero-shot means:
  - no example answers;
  - no lists of earlier answer openings;
  - no retry with feedback;
  - no audit judges.
- Scale: 741 questions across 3 datasets and 17 question types.
  - 50 per type wherever the gated pool allows.
  - JIGSAWS D2 has 20, JIGSAWS D5 has 5, MB140 D2 has 31 and MB140 D5 has 35.

## Results at a glance

| Dataset | Type | n | Label | Localization | Scope | Defects (any ≤3) | All 5s |
|---|---|---|---|---|---|---|---|
| JIGSAWS | D1 overall feedback | 50 | 4.88 | – | 4.98 | 2 | 47 |
| JIGSAWS | D2 improve first | 20 | 4.95 | – | 5.00 | 0 | 19 |
| JIGSAWS | D5 went best | 5 | 5.00 | – | 4.80 | 0 | 4 |
| JIGSAWS | VTS locate | 50 | 5.00 | 5.00 | 5.00 | 0 | 50 |
| MultiBypass140 | D1 | 50 | 5.00 | – | 5.00 | 0 | 49 |
| MultiBypass140 | D2 | 31 | 5.00 | 5.00 | 5.00 | 0 | 31 |
| MultiBypass140 | D5 | 35 | 4.85 | – | 4.64 | 2 | 22 |
| MultiBypass140 | B1 urgent concern | 50 | 4.84 | – | **3.96** | **10** | 11 |
| MultiBypass140 | B3 rank risks | 50 | 4.86 | – | 4.76 | 2 | 36 |
| MultiBypass140 | B4 near miss? | 50 | 4.98 | – | 4.58 | 3 | 32 |
| MultiBypass140 | B5 when to pause (safety) | 50 | 4.98 | 4.96 | **4.26** | **10** | 25 |
| MultiBypass140 | B6 when to pause (coaching) | 50 | 4.96 | 5.00 | 4.86 | 1 | 44 |
| Endoscapes2023 | B1 urgent concern | 50 | 4.38 | – | **3.34** | **30** | **0** |
| Endoscapes2023 | B2 safe to proceed? | 50 | 4.98 | – | 5.00 | 0 | 49 |
| Endoscapes2023 | B3 rank risks | 50 | 4.94 | – | 4.46 | 2 | 25 |
| Endoscapes2023 | B5 when to pause | 50 | 4.50 | 5.00 | **3.90** | **12** | 9 |
| Endoscapes2023 | B7 safe to clip? | 50 | 4.92 | – | 4.70 | 1 | 34 |

Three answers came back empty (1 MB140 D1, 2 MB140 D5). One JIGSAWS D1 answer has no review.

## Three takeaways

1. **The facts are almost always right; what fails is staying in scope.**
   - Label scores are 4.4–5.0 for every type.
   - Nearly all defects are scope defects. The model adds clinical detail the annotations don't support:
     - downstream complications (leak, sepsis, bile-duct injury);
     - corrective technique;
     - tissue appearance.
   - This happens mostly on the open-ended safety questions (B1, B5).
2. **Structured questions sit at the ceiling: D1/D2/D5 and VTS.**
   - These prompts carry an answer plan, which leaves the model little room to go wrong.
   - So these scores mostly measure whether it follows the plan, not whether it reasons clinically.
3. **The reviewer is the same model, and it isn't fully consistent.**
   - It penalizes added complications in B5, yet accepts similar "immediate patient-safety concern" coaching in B6.
   - It sees the annotation facts but not the answer plan. The 2 JIGSAWS D1 "defects" are answers that followed the plan (verdict plus only the areas rated 4–5), so they are probably reviewer false positives.
   - No reviewer self-test on deliberately broken answers has been run yet.

## Representative questions

### 1. Ceiling case: JIGSAWS VTS (50/50 all 5s)
**Q:** Show the trainee the step in this recording where the surgeon loses the most time.
**Facts:** orienting the needle, 0:06.8–0:18.3, 11.5 s vs. about 3.8 s for the strongest attempts.
**A:** "The step that runs furthest over time is orienting the needle from 0:06.8 to 0:18.3. It has a duration of 11.5 seconds, where the strongest recorded attempts at the same action take about 3.8 seconds."
**Review:** 5 / 5 / 5. The answer restates the facts exactly.

### 2. Ceiling case: MultiBypass140 D2 (31/31 all 5s)
**Q:** Given the trainee's performance during 0:05:48–0:52:42, which adverse event should the trainee address first, and why?
**A:** "You should address the mechanical injury and bleeding at 0:10:20, during jejunojejunal stapling, first. It was the most severe of the three events in this phase: a through and through injury to vital tissue, and a bleed with a low amount of blood lost, about two minutes."
**Review:** 5 / 5 / 5. The SEVERE descriptions are quoted correctly and the right event was chosen.

### 3. Typical scope failure: Endoscapes B1 (worst type; 0 of 50 all 5s)
**Q:** At timestamp 18:26, identify the most urgent patient-safety concern.
**Facts:**
- CVS structures identified: 0.
- Triangle cleared: 0.
- Gallbladder separated: 1.
- Classes present: cystic artery, cystic duct, cystic plate, gallbladder, tool.

**A (excerpt):** "…the cystic duct and the cystic artery have not yet been clearly identified… any further dissection, clip placement, or cut carries a direct risk of injuring the common bile duct or the hepatic artery, both of which are catastrophic injuries. The immediate corrective step is to pause… Clearing the triangle and separating the gallbladder from the liver bed can follow…"
**Review:** label 3, scope 2.
- The core concern is right.
- But it invents catastrophic consequences and corrective steps.
- It also implies the gallbladder still needs separating, though the label says separation was achieved.

### 4. Scope failure with confident voice: MultiBypass140 B5
**Q:** At which timestamp should a supervisor pause the trainee to prevent possible patient harm? …
**Facts:**
- Thermal injury, severity 3, not rectified.
- Gastric opening, 22:48–26:37.
- The labels do not describe its visual appearance or physical cause.

**A (excerpt):** "At 22:48–26:37, the pause is non-negotiable… Continuing through an uncorrected thermal injury puts you at risk for a full-thickness perforation, a necrotic edge… reassess your energy parameters… Use brief, controlled energy applications… if the tissue at the cut edge looks weakened, discolored, or charred, stop…"
**Review:** label 5, localization 5, scope 2. The timing and event are right; the visual and mechanical details and the complications are invented.

### 5. Wording rule broken, facts fine: MultiBypass140 B4
**Q:** Does this clip show a near-miss event that could have resulted in patient harm?
**A (excerpt):** "No. This segment documents an actual mechanical injury that occurred during retrogastric dissection… what is recorded here is the opposite—a real injury to tissue, not a close call."
**Review:** label 5, scope 3. The answer talks about the record ("documents", "recorded") instead of the surgery.

### 6. A real reasoning error: MultiBypass140 D5
**Q:** Based on the events occurring during 0:00:00–2:04:44, which phase went best for the trainee, and why does it matter?
**Facts:** "In 74 of the 139 operations… that phase had a **more severe** adverse event than yours."
**A:** "…That matters because 74 of the 139 operations… had an adverse event in that phase **that was not in this operation**."
**Review:** label 3, scope 3. The model garbled the comparison and reversed what the statistic means. This is one of the few true factual errors (2 of 33).

### 7. Where the reviewer itself is questionable: JIGSAWS D1
**Q:** If you were the supervising surgeon, based only on the events occurring during 0:00–1:23, what feedback would you give on the trainee's tissue handling, instrument/needle handling, economy of motion, and flow of operation?
**A:** "You were competent. What stood out was how you planned your course of operation."
**Review:** label 2.
- The reviewer wanted the level-3 descriptions for tissue handling and movement.
- But D1 is designed to give only the verdict and the areas rated 4–5, so this answer follows the plan.
- This shows the cost of a reviewer that sees the facts without the question's answer plan.

## Caveats

- **Self-review.** The same model generates and reviews. An independent reviewer, or a human spot check on about 20 records, is the next step.
- **Answer plans constrain D and VTS.** Their scores are not comparable to the B scores.
- **Smaller pools.** The gated eligibility rules leave fewer than 50 questions for JIGSAWS D2/D5 and MB140 D2/D5.
