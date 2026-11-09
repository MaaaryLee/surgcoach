# Question inventory: Types A–D and VTS (2026-11-06)

This file collects every question wording we have for each template. Where two sources word
the same question differently, every version is listed. The picked wording is ticked `[x]`.

## Picks

The rule: take the wording that has been injected into the system prompt and asked in runs.
- **B and C:** that is `template questions.md`. July's B and C runs asked exactly what it held then.
- **Where a question was trimmed after July:** B2, B5, C1, C2, C5 and C7. The pick is the trimmed version, and the July wording is listed right under it.
- **D:** the coaching runs don't take D from that file. The pick is the wording asked in every run since 9-30 (JIGSAWS) and 10-28 (MB140).

| Template | Picked wording | Where it comes from |
|---|---|---|
| B1 | At timestamp [t], identify the most urgent patient-safety concern. | Asked in July |
| B2 | Based on the frame at [t], is it medically safe to proceed with [action]? | Trimmed. July's version added "Answer yes/no, including anatomical exposure … justify using visible evidence." |
| B3 | Rank the intraoperative risks from highest to lowest clinical priority. | Asked in July |
| B4 | Does this clip show a near-miss event that could have resulted in patient harm? | Asked in July |
| B5 | At which timestamp should a supervisor pause the trainee to prevent possible patient harm? | Trimmed. July's version added "Explain: what visible event triggered the stop, …" |
| B6 | At which timestamp should a supervisor pause the trainee for coaching, and why? | Asked in July; the same in every file |
| B7 | Based on the frame at [t], is it medically safe to proceed with clipping or cutting the cystic duct or artery? | Asked in July; the same in every file |
| C1 | How would you rate the trainee's tissue handling skills in [clip] from 1-5? Provide: score, visible evidence, potential tissue injury risk, and one concrete improvement. | Edited after July (uncommitted). July asked "Rate the trainee's tissue handling in [clip] from 1-5. …" |
| C2 | How would you rate the instrument handling in [clip] from 1-5? Provide: score, key evidence, unsafe movement if present, and corrective suggestion. | Edited after July (uncommitted). July's version also listed what to consider. |
| C3 | Does the trainee use efficient motion in this clip? Identify unnecessary movements and suggest a more efficient alternative. | Asked in July |
| C4 | How well does the trainee coordinate both hands? Identify whether one hand is passive, obstructive, or not optimizing exposure. | Asked in July. Disabled for JIGSAWS, which has no label for it. |
| C5 | Does the trainee accurately direct the instrument tip to the intended plane or target? | Trimmed (uncommitted). Disabled for JIGSAWS, which has no label for it. |
| C6 | Does the trainee demonstrate smooth procedural flow and anticipation of the next operative step? | Asked in July |
| C7 | Based on the clip, what level of supervision would the trainee likely require? | Trimmed (uncommitted). July also listed the five levels and asked for visible evidence. |
| D1 | **JIGSAWS:** If you were the supervising surgeon, based only on the events occurring during [a certain video span], what feedback would you give on the trainee's tissue handling, instrument/needle handling, economy of motion, and flow of operation?<br>**MB140:** If you were the supervising surgeon, based only on the events occurring during [span], what feedback would you give on the trainee's performance? | Every coaching run since 9-30 (JIGSAWS) and 10-28 (MB140) |
| D2 | **JIGSAWS:** Given the trainee's performance during [a certain video span], among tissue handling, instrument/needle handling, economy of motion, and flow of operation, which area should the trainee improve first, why, and what should the trainee do immediately to address it?<br>**MB140:** Given the trainee's performance during [span], which adverse event should the trainee address first, and why? | The same |
| D5 | **JIGSAWS:** Based on this trainee's performance, what is their strongest technical skill, and why does it matter?<br>**MB140:** Based on the events occurring during [span], which phase went best for the trainee, and why does it matter? | The same |

D3 and D4 are dropped; D3's corrective action now lives in JIGSAWS D2.

**Two conflicts in the picks, for Yulu:**
- **C asks for things the 11-04 review and the system prompt rule out.** C1 and C2 ask for a score from 1 to 5, while the review asked for descriptions, not numbers, and the system prompt keeps scores out of answers. C1, C2 and C3 also ask for visible evidence or suggestions, which an annotation-only answer can't give.
- **The system prompt still asks for the trimmed B2 and B5 endings.** Its B2 instruction keeps the removed ending ("Answer yes/no, … justify using visible evidence"). Its B5 instruction still asks for the visible trigger and the structure at risk.

## Sources

Each version is tagged with where it comes from.

| Tag | File | What it is |
|---|---|---|
| **[INJECTED]** | `Prompts_And_Pipeline/template questions.md` | Pasted into the system prompt (`system-prompt-A-D.md` line 71, `{{include: template questions.md}}`). Also read by the JIGSAWS runner and by the local copies of the MB140 and Endoscapes runners. |
| **[INJECTED, committed]** | the same file, as committed | Shown only where it differs from the local copy. The local copy has uncommitted edits to C1, C2, C5 and C7. This committed wording is what July's C runs asked. |
| **[CANONICAL]** | `Prompts_And_Pipeline/template-questions-A-D.md` | The other "single source of truth". It is read by the committed (upstream) MB140 and Endoscapes runners, and its D wording was the basis for the JIGSAWS D work. |
| **[ASKED JULY]** | `results/outputs B1-B7 53 QA thinking-enabled rule11 - 7-29-2026` | The B wording the model was actually asked in the last full B run. |
| **[SEP original] / [SEP humanized] / [SEP formal] / [SEP informal]** | `~/Downloads/Prompt BCD.txt` (2026-09-16). The humanized lines are only in the earlier copy pasted into Codex on 2026-09-02, `~/.codex/attachments/bc7e04a0-e4b8-4bcb-8e91-a87d4122f652/pasted-text.txt`. | The September question bank. Each original has a humanized, a formal and an informal rewrite. The 09-16 copy is the 09-02 one with the humanized lines removed. The originals are also in `Prompts_And_Pipeline/template-question-variants-B-D.md`. |
| **[HUMANIZED]** | `docs/representative-questions-by-dataset.md` | [SEP humanized] lines picked per dataset, with placeholders such as [event span] or [timestamp/frame] in place of [t]. |
| **[IN USE: JIGSAWS] / [IN USE: MB140]** | `scripts/jigsaws_template_d_plan.py` / `scripts/run_mb140_coaching_d.py` | The D wording the current coaching runs ask. |

## Where the wording disagrees

1. **There are two "canonical" question files, and the runners disagree about which one to read.**
   - **System prompt:** it injects `template questions.md`.
   - **Committed runners:** the MB140 and Endoscapes runners read `template-questions-A-D.md`.
   - **Local runners:** the local copies read `template questions.md`. These two runner files are in an unresolved merge conflict in the local checkout.
2. **B1–B5: worded in one file, missing from the other.** [INJECTED] words all of them. [CANONICAL] marks them "not yet specified" because the labels can't support them. The MB140 runner also refuses B1–B5. Meanwhile the September bank notes say MB140 supports B1, B3, B5 and B6.
3. **B2 and B5: shortened since July.** The July run asked longer versions; the injected file now has short ones. B2's old ending ("Answer yes/no, including anatomical exposure … justify using visible evidence") still sits in the system prompt as B2's instruction, and it asks for visible evidence, which annotation-only mode can't give.
4. **Template titles differ between files** (for example, B6 is "Cause Of Error" in one and "Coaching Pause" in another). The titles are listed under each template.
5. **C is worded for a model that watches the video.** [INJECTED] asks for a 1–5 score and visible evidence. [CANONICAL] has annotation-only rewrites, and keeps the vision wording only as a human-only note.
6. **D has three wordings in use.** The generic one is in both files. The JIGSAWS run names the four technique areas, and its D2 absorbs D3. The MB140 run asks about adverse events and phases. D3 and D4 are dropped.
7. **Some September "formal" rewrites are identical to the original.** These are B3.3, B5.4, B6.5, B7.2 and D2.3, marked *(same as original)* below.
8. **No variants anywhere for C4 and C5.** They are disabled for JIGSAWS, which has no label for either skill.

---

## Type A: Basic Perception (for reference)

Only [CANONICAL] has Type A. None of it is generated today, because most of it needs the
model to see the frame. Each template lists alternative questions to pick from.

- **A1 Anatomy Identification:** "At timestamp [t], which anatomical structure or region is most relevant to the current action?" / "What is the instrument being used?" / "What is the trainee's action? Explain with visual cues."
- **A2 Procedural/Order Identification:** "What phase of the procedure is shown at [t]?" / "What is the most appropriate next step after this clip?"
- **A3 Comparison:** "Compare the trainee's performance before vs. after feedback. What improved?" / "Compare clip A and clip B by expert vs. trainee. Which one shows better technique?"
- **A4 Timestamp:** "At what timestamp does the trainee first lose safe visualization?" / "Identify the timestamp where supervisor feedback would be most useful."

---

## Type B: Safety and Danger Detection

### B1

Titles: "Dangerous Frame Critique" [INJECTED], "safety_frame_critique" [CANONICAL], "Most Urgent Safety Concern" [SEP].

**Single wording**
- [x] [INJECTED] At timestamp [t], identify the most urgent patient-safety concern.
- [ ] [ASKED JULY] At timestamp [t], identify the most urgent patient-safety concern. *(same as INJECTED)*
- [ ] [CANONICAL] *not yet specified:* requires explicit safety or error labels.

**September bank**
- B1.1
  - [ ] [SEP original] What is the most urgent patient-safety concern at [t]?
  - [ ] [SEP humanized] What's the biggest safety worry we should be watching for at [t]?
  - [ ] [SEP formal] What constitutes the most urgent patient-safety concern at [t]?
  - [ ] [SEP informal] What's the scariest thing going on at [t]?
- B1.2
  - [ ] [SEP original] Which documented event at [t] requires the fastest response?
  - [ ] [SEP humanized] Looking at what's been recorded at [t], which event needs attention first?
  - [ ] [SEP formal] Which documented event occurring at [t] necessitates the most immediate response?
  - [ ] [SEP informal] Out of everything logged at [t], what needs to be dealt with ASAP?
- B1.3
  - [ ] [SEP original] What should concern the supervising surgeon most at [t]?
  - [ ] [SEP humanized] If you were the supervising surgeon at [t], what would worry you most?
  - [ ] [SEP formal] What should be of greatest concern to the supervising surgeon at [t]?
  - [ ] [SEP informal] What would make the supervising surgeon most nervous at [t]?
- B1.4
  - [ ] [SEP original] At [t], which safety issue should take immediate priority?
  - [ ] [SEP humanized] At [t], which safety issue needs to be handled right away?
  - [ ] [SEP formal] At [t], which safety issue should be accorded immediate priority?
  - [ ] [SEP informal] At [t], what's the top thing to fix first?
- B1.5
  - [ ] [SEP original] What is the primary threat to safe continuation at [t]?
  - [ ] [SEP humanized] What's most likely to put safe continuation at risk at [t]?
  - [ ] [SEP formal] What constitutes the primary threat to the safe continuation of the procedure at [t]?
  - [ ] [SEP informal] What's the biggest thing that could throw off the surgery at [t]?

**Humanized, per dataset**
- [ ] [HUMANIZED, Endoscapes2023] If you were the supervising surgeon at [timestamp/frame], what would worry you most?
- [ ] [HUMANIZED, MultiBypass140] What's the biggest safety worry we should be watching for at [event span]?

### B2

Titles: "Is It Safe To Proceed?" [INJECTED], "safe_to_proceed" [CANONICAL], "Safe to Proceed" [SEP].

**Single wording**
- [x] [INJECTED] Based on the frame at [t], is it medically safe to proceed with [action]?
- [ ] [ASKED JULY] Based on the frame at [t], is it medically safe to proceed with [action]? Answer yes/no, including anatomical exposure, tissue plane, hemostasis, instrument position, and energy safety uncertain and justify using visible evidence.
- [ ] [CANONICAL] *not yet specified:* see B7, the Critical View of Safety case that Endoscapes2023 supports.

**September bank**
- B2.1
  - [ ] [SEP original] Is it medically safe to proceed with [action] at [t], and why?
  - [ ] [SEP humanized] At [t], is it actually safe to go ahead with [action]? What's the reasoning?
  - [ ] [SEP formal] Is it medically appropriate to proceed with [action] at [t], and what is the rationale?
  - [ ] [SEP informal] Is [action] at [t] actually safe to do? Why or why not?
- B2.2
  - [ ] [SEP original] Should the trainee continue with [action] at [t], or pause first?
  - [ ] [SEP humanized] At [t], should the trainee keep going with [action], or stop and check first?
  - [ ] [SEP formal] Should the trainee continue performing [action] at [t], or should a pause be warranted?
  - [ ] [SEP informal] Should the trainee just keep doing [action] at [t], or hold up first?
- B2.3
  - [ ] [SEP original] What must be addressed before proceeding with [action] at [t]?
  - [ ] [SEP humanized] What needs to be sorted out before [action] can safely continue at [t]?
  - [ ] [SEP formal] What issues must be resolved prior to proceeding with [action] at [t]?
  - [ ] [SEP informal] What's gotta get fixed before [action] goes ahead at [t]?
- B2.4
  - [ ] [SEP original] Does the available evidence support continuing with [action] at [t]?
  - [ ] [SEP humanized] Based on what we can see, does it look okay to keep going with [action] at [t]?
  - [ ] [SEP formal] Does the available evidence support the continuation of [action] at [t]?
  - [ ] [SEP informal] Does the evidence actually back up continuing [action] at [t]?
- B2.5
  - [ ] [SEP original] Would proceeding with [action] at [t] introduce an unacceptable risk?
  - [ ] [SEP humanized] If they go ahead with [action] at [t], would that create a risk that's too much?
  - [ ] [SEP formal] Would proceeding with [action] at [t] introduce a degree of risk deemed unacceptable?
  - [ ] [SEP informal] Would pushing forward with [action] at [t] be too risky?

**Humanized, per dataset**
- [ ] [HUMANIZED, CholecT50, only with a surgeon's reference judgement] At [timestamp], is it actually safe to go ahead with [annotated action]? What's the reasoning?

### B3

Titles: "Risk Ranking" in all files; "risk_ranking" [CANONICAL].

**Single wording**
- [x] [INJECTED] Rank the intraoperative risks from highest to lowest clinical priority.
- [ ] [ASKED JULY] *(same as INJECTED)*
- [ ] [CANONICAL] *not yet specified:* requires a risk taxonomy no current dataset provides.

**September bank**
- B3.1
  - [ ] [SEP original] Rank the documented intraoperative risks from most to least urgent.
  - [ ] [SEP humanized] Can you rank the recorded risks in this surgery from most to least urgent?
  - [ ] [SEP formal] Rank the documented intraoperative risks in descending order of urgency.
  - [ ] [SEP informal] Put the recorded risks in order — worst first.
- B3.2
  - [ ] [SEP original] Which recorded risk requires attention first, and what is the correct order for the remaining risks?
  - [ ] [SEP humanized] Which risk needs to be dealt with first, and how should the rest be ordered?
  - [ ] [SEP formal] Which documented risk requires the most immediate attention, and what is the appropriate sequence for the remaining risks?
  - [ ] [SEP informal] Which risk needs handling first, and what order do the rest go in?
- B3.3
  - [ ] [SEP original] In what sequence should the identified safety concerns be addressed?
  - [ ] [SEP humanized] What order should these safety concerns be tackled in?
  - [ ] [SEP formal] *(same as original)*
  - [ ] [SEP informal] What's the order to knock out these safety issues in?
- B3.4
  - [ ] [SEP original] Prioritize the documented adverse events according to their clinical urgency.
  - [ ] [SEP humanized] Can you prioritize these adverse events based on how urgent they are clinically?
  - [ ] [SEP formal] Prioritize the documented adverse events in accordance with their clinical urgency.
  - [ ] [SEP informal] Rank these adverse events by how urgent they are, medically speaking.
- B3.5
  - [ ] [SEP original] If only one risk could be managed immediately, which should come first, and how should the others be ordered?
  - [ ] [SEP humanized] If you could only fix one risk right now, which one would it be, and how would the rest line up?
  - [ ] [SEP formal] If only a single risk could be managed immediately, which should take precedence, and how should the remaining risks be ordered?
  - [ ] [SEP informal] If you could only fix one thing right now, which risk would it be, and what order do the rest come in?

**Humanized, per dataset**
- [ ] [HUMANIZED, Endoscapes2023] Can you rank the recorded risks in this surgery from most to least urgent?
- [ ] [HUMANIZED, MultiBypass140] Which risk needs to be dealt with first, and how should the rest be ordered?

### B4

Titles: "Near-Miss Detection" in all files; "near_miss_detection" [CANONICAL]. No dataset labels near misses, so B4 can only be asked as a negative control.

**Single wording**
- [x] [INJECTED] Does this clip show a near-miss event that could have resulted in patient harm?
- [ ] [ASKED JULY] *(same as INJECTED)*
- [ ] [CANONICAL] *not yet specified:* requires near-miss labels.

**September bank**
- B4.1
  - [ ] [SEP original] Does [clip] contain a documented near miss that could have caused patient harm?
  - [ ] [SEP humanized] Is there a near-miss moment in [clip] that could've hurt the patient?
  - [ ] [SEP formal] Does [clip] contain a documented near miss with the potential to have caused patient harm?
  - [ ] [SEP informal] Is there a close call in [clip] that almost hurt the patient?
- B4.2
  - [ ] [SEP original] Was patient harm narrowly avoided during [clip]?
  - [ ] [SEP humanized] Did the patient almost get hurt during [clip]?
  - [ ] [SEP formal] Was patient harm narrowly averted during the course of [clip]?
  - [ ] [SEP informal] Did they just barely avoid hurting the patient in [clip]?
- B4.3
  - [ ] [SEP original] Is there evidence of a near miss in [clip], or was an actual adverse event documented?
  - [ ] [SEP humanized] Does [clip] show a near miss, or did something actually go wrong?
  - [ ] [SEP formal] Does [clip] provide evidence of a near miss, or was an actual adverse event documented?
  - [ ] [SEP informal] Was [clip] a close call, or did something actually happen?
- B4.4
  - [ ] [SEP original] Should the event in [clip] be classified as a near miss? Explain the distinction.
  - [ ] [SEP humanized] Should we call the event in [clip] a near miss? What's the difference from an actual harm event?
  - [ ] [SEP formal] Should the event depicted in [clip] be classified as a near miss? Please explain the relevant distinction.
  - [ ] [SEP informal] Does what happened in [clip] count as a near miss? What's the difference?
- B4.5
  - [ ] [SEP original] Did the trainee's course create a documented risk that was corrected before harm occurred?
  - [ ] [SEP humanized] Did the trainee create a risky situation that got caught and fixed before anyone got hurt?
  - [ ] [SEP formal] Did the trainee's course of action create a documented risk that was subsequently corrected before harm occurred?
  - [ ] [SEP informal] Did the trainee mess up in a way that got caught before it caused actual harm?

**Humanized, per dataset:** none.

### B5

Titles: "Stop Point" [INJECTED], "stop_point" [CANONICAL], "Supervisor Stop Point" [SEP].

**Single wording**
- [x] [INJECTED] At which timestamp should a supervisor pause the trainee to prevent possible patient harm?
- [ ] [ASKED JULY] At which timestamp should a supervisor pause the trainee to prevent possible patient harm? Explain: what visible event triggered the stop, what injury is being prevented, which structure is at risk, and what corrective coaching should be given.
- [ ] [CANONICAL] *not yet specified:* requires explicit safety or error labels.

**September bank**
- B5.1
  - [ ] [SEP original] At what timestamp should the supervisor first pause the trainee to protect the patient?
  - [ ] [SEP humanized] At what point should the supervisor step in and pause the trainee to keep the patient safe?
  - [ ] [SEP formal] At what timestamp should the supervisor first intervene to pause the trainee in order to protect the patient?
  - [ ] [SEP informal] When should the supervisor first hit pause on the trainee to keep the patient safe?
- B5.2
  - [ ] [SEP original] When does intervention become necessary in this video?
  - [ ] [SEP humanized] At what point in this video does the supervisor really need to step in?
  - [ ] [SEP formal] At what point does supervisory intervention become necessary in this video?
  - [ ] [SEP informal] When does someone actually need to step in during this video?
- B5.3
  - [ ] [SEP original] Identify the earliest point at which the trainee should be stopped from continuing.
  - [ ] [SEP humanized] What's the earliest moment the trainee should've been stopped?
  - [ ] [SEP formal] Identify the earliest point at which the trainee should have been stopped from continuing.
  - [ ] [SEP informal] What's the earliest spot where the trainee should've been shut down?
- B5.4
  - [ ] [SEP original] Which timestamp marks the clearest need for immediate supervisor intervention?
  - [ ] [SEP humanized] Which moment most clearly calls for the supervisor to jump in right away?
  - [ ] [SEP formal] *(same as original)*
  - [ ] [SEP informal] Which moment is the biggest "supervisor needs to jump in now" moment?
- B5.5
  - [ ] [SEP original] Where should the supervisor interrupt the procedure before the documented event progresses?
  - [ ] [SEP humanized] Where should the supervisor cut in before this documented event gets worse?
  - [ ] [SEP formal] At what point should the supervisor interrupt the procedure before the documented event progresses further?
  - [ ] [SEP informal] Where should the supervisor cut things off before this gets worse?

**Humanized, per dataset**
- [ ] [HUMANIZED, Endoscapes2023] At what point in this video does the supervisor really need to step in?
- [ ] [HUMANIZED, MultiBypass140] Where should the supervisor cut in before this documented event gets worse?

### B6

Titles: "Cause Of Error" [INJECTED], "cause_of_error" [CANONICAL], "Coaching Pause" [SEP].

**Single wording**
- [x] [INJECTED] At which timestamp should a supervisor pause the trainee for coaching, and why?
- [ ] [CANONICAL] *(same as INJECTED)*
- [ ] [ASKED JULY] *(same as INJECTED)*

**September bank**
- B6.1
  - [ ] [SEP original] At what timestamp would a coaching pause be most useful, and what should the supervisor address?
  - [ ] [SEP humanized] When would it be most useful to pause for coaching, and what should get discussed?
  - [ ] [SEP formal] At what timestamp would a coaching pause be most beneficial, and what should the supervisor address at that point?
  - [ ] [SEP informal] When's the best spot to pause for coaching, and what should get talked about?
- B6.2
  - [ ] [SEP original] When should the supervisor pause the trainee, and what is the key teaching point?
  - [ ] [SEP humanized] When should the supervisor stop the trainee, and what's the main lesson to teach?
  - [ ] [SEP formal] At what point should the supervisor pause the trainee, and what constitutes the key teaching point?
  - [ ] [SEP informal] When should the supervisor pause things, and what's the main thing to teach?
- B6.3
  - [ ] [SEP original] Which moment provides the best opportunity for corrective coaching?
  - [ ] [SEP humanized] Which moment is the best chance to give some corrective coaching?
  - [ ] [SEP formal] Which moment provides the most suitable opportunity for corrective coaching?
  - [ ] [SEP informal] What's the best moment to jump in with some coaching?
- B6.4
  - [ ] [SEP original] Where in the procedure should the supervisor intervene to discuss the documented problem?
  - [ ] [SEP humanized] Where in the procedure should the supervisor step in to talk about this problem?
  - [ ] [SEP formal] At what point in the procedure should the supervisor intervene to discuss the documented problem?
  - [ ] [SEP informal] Where should the supervisor stop and talk about the problem?
- B6.5
  - [ ] [SEP original] Identify the most useful coaching point in the video and explain why it warrants a pause.
  - [ ] [SEP humanized] What's the most useful coaching moment in this video, and why does it deserve a pause?
  - [ ] [SEP formal] *(same as original)*
  - [ ] [SEP informal] What's the best coaching moment here, and why's it worth stopping for?

**Humanized, per dataset**
- [ ] [HUMANIZED, MultiBypass140] When would it be most useful to pause for coaching, and what should get discussed?

### B7

Titles: "CVS Safety Check" [INJECTED], "cvs_safety_check" [CANONICAL], "Critical View of Safety" [SEP]. Endoscapes2023 only.

**Single wording**
- [x] [INJECTED] Based on the frame at [t], is it medically safe to proceed with clipping or cutting the cystic duct or artery?
- [ ] [CANONICAL] *(same as INJECTED)*
- [ ] [ASKED JULY] *(same as INJECTED)*

**September bank**
- B7.1
  - [ ] [SEP original] At [t], have the requirements for the Critical View of Safety been met sufficiently to proceed?
  - [ ] [SEP humanized] By [t], have the Critical View of Safety requirements actually been met enough to move forward?
  - [ ] [SEP formal] At [t], have the requirements for the Critical View of Safety been satisfied sufficiently to permit proceeding?
  - [ ] [SEP informal] By [t], is the Critical View of Safety good enough to move on?
- B7.2
  - [ ] [SEP original] Is it safe to clip or divide the cystic structures at [t]?
  - [ ] [SEP humanized] At [t], is it actually safe to clip or cut the cystic structures?
  - [ ] [SEP formal] *(same as original)*
  - [ ] [SEP informal] Is it safe to clip or cut the cystic structures at [t]?
- B7.3
  - [ ] [SEP original] What remains unresolved before clipping or cutting can safely begin at [t]?
  - [ ] [SEP humanized] What still needs to be figured out before clipping or cutting can safely start at [t]?
  - [ ] [SEP formal] What remains unresolved prior to the safe initiation of clipping or division at [t]?
  - [ ] [SEP informal] What's still up in the air before they can safely start clipping or cutting at [t]?
- B7.4
  - [ ] [SEP original] Should the surgeon proceed with clipping at [t], or continue the dissection?
  - [ ] [SEP humanized] At [t], should the surgeon go ahead and clip, or keep dissecting first?
  - [ ] [SEP formal] Should the surgeon proceed with clipping at [t], or should the dissection be continued?
  - [ ] [SEP informal] Should the surgeon just clip at [t], or keep dissecting?
- B7.5
  - [ ] [SEP original] Does the evidence at [t] support safe identification of the structures before division?
  - [ ] [SEP humanized] Based on the evidence at [t], have the structures actually been safely identified before dividing them?
  - [ ] [SEP formal] Does the evidence at [t] support the safe identification of the structures prior to division?
  - [ ] [SEP informal] Does the evidence at [t] show the structures were actually ID'd safely before cutting?
- B7.6
  - [ ] [SEP original] Is the Critical View of Safety complete, incomplete, or uncertain at [t]?
  - [ ] [SEP humanized] At [t], would you say the Critical View of Safety is complete, incomplete, or unclear?
  - [ ] [SEP formal] At [t], is the Critical View of Safety to be regarded as complete, incomplete, or uncertain?
  - [ ] [SEP informal] At [t], is the Critical View of Safety done, not done, or unclear?

**Humanized, per dataset**
- [ ] [HUMANIZED, Endoscapes2023] At [timestamp/frame], should the surgeon go ahead and clip, or keep dissecting first?

---

## Type C: Skill Assessment (JIGSAWS)

### C1 Tissue Handling

**Single wording**
- [x] [INJECTED] How would you rate the trainee's tissue handling skills in [clip] from 1-5? Provide: score, visible evidence, potential tissue injury risk, and one concrete improvement.
- [ ] [INJECTED, committed] Rate the trainee's tissue handling in [clip] from 1-5. Provide: score, visible evidence, potential tissue injury risk, and one concrete improvement.
- [ ] [CANONICAL] Based on this trainee's performance during [a certain video span], how strong is their tissue handling, and what should they work on to improve it?

**September bank**
- C1.1
  - [ ] [SEP original] How would you characterize the trainee's tissue handling, and what improvement deserves the most attention?
  - [ ] [SEP humanized] How would you describe the way this trainee handles tissue, and what's the one thing they should work on most?
  - [ ] [SEP formal] How would you characterize the trainee's tissue handling, and which area of improvement warrants the greatest attention?
  - [ ] [SEP informal] How's the trainee's tissue handling overall, and what should they fix most?
- C1.2
  - [ ] [SEP original] What does this performance suggest about the trainee's respect for tissue, and what should they practice next?
  - [ ] [SEP humanized] What does this tell us about how carefully the trainee treats tissue, and what should they practice next?
  - [ ] [SEP formal] What does this performance suggest regarding the trainee's respect for tissue, and what should they practice subsequently?
  - [ ] [SEP informal] What does this say about how gentle the trainee is with tissue, and what should they drill next?
- C1.3
  - [ ] [SEP original] Where does the trainee currently stand in tissue handling, and how can they develop better control?
  - [ ] [SEP humanized] Where's the trainee at right now with tissue handling, and how can they get better control?
  - [ ] [SEP formal] Where does the trainee currently stand with respect to tissue handling, and how might they develop improved control?
  - [ ] [SEP informal] Where's the trainee at with tissue handling, and how do they get better control?
- C1.4
  - [ ] [SEP original] What is the most important tissue-handling lesson to take away from this performance?
  - [ ] [SEP humanized] What's the biggest tissue-handling lesson to pull from this performance?
  - [ ] [SEP formal] What is the most significant tissue-handling lesson to be derived from this performance?
  - [ ] [SEP informal] What's the main tissue-handling takeaway here?

**Humanized, per dataset**
- [ ] [HUMANIZED] How would you describe the way this trainee handles tissue, and what's the one thing they should work on most?

### C2 Instrument and Needle Handling

**Single wording**
- [x] [INJECTED] How would you rate the instrument handling in [clip] from 1-5? Provide: score, key evidence, unsafe movement if present, and corrective suggestion.
- [ ] [INJECTED, committed] Rate instrument handling from 1-5. Consider instrument steadiness, tip visibility, precision, angle of approach, wrist control, avoidance of off-screen movement, and prevention of unintended contact with adjacent tissue. Provide: score, key evidence, unsafe movement if present, and corrective suggestion.
- [ ] [CANONICAL] Based on this trainee's performance during [a certain video span], how strong is their instrument and needle handling, and what should they work on to improve it?

**September bank**
- C2.1
  - [ ] [SEP original] How effectively does the trainee handle the instruments and needle, and what should they refine?
  - [ ] [SEP humanized] How well does the trainee handle the instruments and needle, and what should they work on refining?
  - [ ] [SEP formal] How effectively does the trainee handle the instruments and needle, and which aspects require refinement?
  - [ ] [SEP informal] How good is the trainee with the instruments and needle, and what needs cleaning up?
- C2.2
  - [ ] [SEP original] What is the trainee's main strength or weakness in instrument and needle control?
  - [ ] [SEP humanized] What's the trainee's biggest strength or weakness with instrument and needle control?
  - [ ] [SEP formal] What constitutes the trainee's primary strength or weakness in instrument and needle control?
  - [ ] [SEP informal] What's the trainee's biggest plus or minus with instrument and needle control?
- C2.3
  - [ ] [SEP original] Which aspect of instrument handling would benefit most from additional practice?
  - [ ] [SEP humanized] Which part of instrument handling would benefit most from extra practice?
  - [ ] [SEP formal] Which aspect of instrument handling would derive the greatest benefit from additional practice?
  - [ ] [SEP informal] What part of instrument handling needs the most extra reps?
- C2.4
  - [ ] [SEP original] What coaching would help this trainee develop more precise instrument and needle handling?
  - [ ] [SEP humanized] What coaching would help this trainee get more precise with instruments and the needle?
  - [ ] [SEP formal] What coaching would facilitate the development of more precise instrument and needle handling in this trainee?
  - [ ] [SEP informal] What kind of coaching would sharpen up this trainee's instrument and needle work?

**Humanized, per dataset**
- [ ] [HUMANIZED] How well does the trainee handle the instruments and needle, and what should they work on refining?

### C3 Economy of Motion

**Single wording**
- [x] [INJECTED] Does the trainee use efficient motion in this clip? Identify unnecessary movements and suggest a more efficient alternative.
- [ ] [CANONICAL] Based on this trainee's performance during [a certain video span], how efficient is their motion, and what should they work on to improve it?

**September bank**
- C3.1
  - [ ] [SEP original] How efficient is the trainee's motion, and what should they do to make their movements more economical?
  - [ ] [SEP humanized] How efficient is the trainee's movement, and what should they do to cut out the wasted motion?
  - [ ] [SEP formal] How efficient is the trainee's motion, and what measures should be taken to render their movements more economical?
  - [ ] [SEP informal] How efficient is the trainee's movement, and what should they do to stop wasting motion?
- C3.2
  - [ ] [SEP original] What does this performance suggest about the trainee's economy of motion?
  - [ ] [SEP humanized] What does this performance tell us about how economical the trainee's movements are?
  - [ ] [SEP formal] What does this performance suggest regarding the trainee's economy of motion?
  - [ ] [SEP informal] What does this say about how efficient the trainee's movements are?
- C3.3
  - [ ] [SEP original] Which motion-related skill should the trainee prioritize during their next practice session?
  - [ ] [SEP humanized] Which motion-related skill should the trainee focus on next time they practice?
  - [ ] [SEP formal] Which motion-related skill should the trainee prioritize during their subsequent practice session?
  - [ ] [SEP informal] What motion skill should the trainee work on next time?
- C3.4
  - [ ] [SEP original] How could the trainee make their operative movements more deliberate and efficient?
  - [ ] [SEP humanized] How could the trainee make their movements more deliberate and efficient during the operation?
  - [ ] [SEP formal] By what means could the trainee render their operative movements more deliberate and efficient?
  - [ ] [SEP informal] How can the trainee make their moves more purposeful and less wasted?

**Humanized, per dataset**
- [ ] [HUMANIZED] What does this performance tell us about how economical the trainee's movements are?

### C4 Bimanual Dexterity (disabled: no JIGSAWS label)

- [x] [INJECTED] How well does the trainee coordinate both hands? Identify whether one hand is passive, obstructive, or not optimizing exposure.
- [CANONICAL] *not yet specified.* No September or humanized versions.

### C5 Depth Perception / Targeting (disabled: no JIGSAWS label)

- [x] [INJECTED] Does the trainee accurately direct the instrument tip to the intended plane or target?
- [ ] [INJECTED, committed] Does the trainee accurately direct the instrument tip to the intended plane or target? Identify evidence of overshooting, undershooting, repeated correction, unstable targeting, poor depth judgment, or contact with unintended tissue.
- [CANONICAL] *not yet specified.* No September or humanized versions.

### C6 Flow of Operation

**Single wording**
- [x] [INJECTED] Does the trainee demonstrate smooth procedural flow and anticipation of the next operative step?
- [ ] [CANONICAL] Based on this trainee's performance during [a certain video span], how smooth is their flow of operation, and what should they work on to improve it?

**September bank**
- C6.1
  - [ ] [SEP original] How smoothly does the trainee progress through the procedure, and what would improve their operative flow?
  - [ ] [SEP humanized] How smoothly is the trainee moving through the procedure, and what would help their flow?
  - [ ] [SEP formal] How smoothly does the trainee progress through the procedure, and what measures would improve their operative flow?
  - [ ] [SEP informal] How smooth is the trainee going through the procedure, and what would help their flow?
- C6.2
  - [ ] [SEP original] What is the most important opportunity to improve the trainee's procedural flow?
  - [ ] [SEP humanized] What's the biggest opportunity to improve how smoothly the trainee moves through the procedure?
  - [ ] [SEP formal] What constitutes the most significant opportunity to improve the trainee's procedural flow?
  - [ ] [SEP informal] What's the number one thing that would smooth out the trainee's flow?
- C6.3
  - [ ] [SEP original] How well does the trainee maintain continuity through the task?
  - [ ] [SEP humanized] How well does the trainee keep things flowing without breaks or hesitation?
  - [ ] [SEP formal] How well does the trainee maintain continuity throughout the task?
  - [ ] [SEP informal] Does the trainee keep a good rhythm through the task?
- C6.4
  - [ ] [SEP original] What coaching would help the trainee develop a smoother and more organized operative rhythm?
  - [ ] [SEP humanized] What coaching would help the trainee build a smoother, more organized rhythm?
  - [ ] [SEP formal] What coaching would facilitate the development of a smoother and more organized operative rhythm in the trainee?
  - [ ] [SEP informal] What coaching would help the trainee get into a smoother groove?

**Humanized, per dataset**
- [ ] [HUMANIZED] How smoothly is the trainee moving through the procedure, and what would help their flow?

### C7 Autonomy / Supervision Level

**Single wording**
- [x] [INJECTED] Based on the clip, what level of supervision would the trainee likely require?
- [ ] [INJECTED, committed] Based on the clip, what level of supervision would the trainee likely require? Classify as: Independent / minimal verbal cueing / repeated verbal cueing / hands-on guidance / supervisor takeover. Justify using visible evidence, including technical control, anatomical awareness, safety risk, ability to correct errors, and whether the trainee can proceed without compromising patient safety.
- [ ] [CANONICAL] Based on this trainee's overall performance, what level of supervision would they likely require: independent, minimal verbal cueing, repeated verbal cueing, hands-on guidance, or supervisor takeover?

**September bank**
- C7.1
  - [ ] [SEP original] What level of supervision is appropriate for this trainee at their current stage?
  - [ ] [SEP humanized] How much supervision does this trainee actually need at their current stage?
  - [ ] [SEP formal] What level of supervision is appropriate for this trainee given their current stage of development?
  - [ ] [SEP informal] How much hand-holding does this trainee need right now?
- C7.2
  - [ ] [SEP original] How much guidance would this trainee likely need while completing a similar task?
  - [ ] [SEP humanized] How much guidance would this trainee probably need on a similar task?
  - [ ] [SEP formal] How much guidance would this trainee likely require while completing a comparable task?
  - [ ] [SEP informal] How much help would this trainee probably need on something similar?
- C7.3
  - [ ] [SEP original] Could this trainee proceed independently, or would active supervision still be appropriate?
  - [ ] [SEP humanized] Could this trainee handle it on their own, or do they still need active supervision?
  - [ ] [SEP formal] Could this trainee proceed independently, or would active supervision remain appropriate?
  - [ ] [SEP informal] Could this trainee just fly solo, or do they still need someone watching?
- C7.4
  - [ ] [SEP original] What degree of supervisor involvement best matches the trainee's overall performance?
  - [ ] [SEP humanized] How involved should the supervisor be, given the trainee's overall performance?
  - [ ] [SEP formal] What degree of supervisor involvement most appropriately corresponds to the trainee's overall performance?
  - [ ] [SEP informal] How hands-on should the supervisor be, given how the trainee did overall?

**Humanized, per dataset**
- [ ] [HUMANIZED] How involved should the supervisor be, given the trainee's overall performance?

---

## Type D: Coaching Feedback

We keep D1, D2 and D5. D3 and D4 are listed at the end, for the record only.

### D1 Overall Coaching Feedback

**Single wording**
- [ ] [INJECTED] and [CANONICAL] If you were the supervising surgeon, based only on the events occurring during [a certain video span], what feedback would you give?
- [x] [IN USE: JIGSAWS] If you were the supervising surgeon, based only on the events occurring during [a certain video span], what feedback would you give on the trainee's tissue handling, instrument/needle handling, economy of motion, and flow of operation?
- [x] [IN USE: MB140] If you were the supervising surgeon, based only on the events occurring during [span], what feedback would you give on the trainee's performance?

**September bank** (all were run on 2026-09-20, `results/template-d-rating-gated-2026-09-20`)
- D1.1
  - [ ] [SEP original] If you were coaching this trainee after the video, what would you discuss first?
  - [ ] [SEP humanized] If you sat down with this trainee after watching the video, what would you bring up first?
  - [ ] [SEP formal] If one were coaching this trainee following the video, what should be discussed first?
  - [ ] [SEP informal] If you were coaching this trainee right after, what would you bring up first?
- D1.2
  - [ ] [SEP original] What is the most useful feedback you could give this trainee?
  - [ ] [SEP humanized] What's the most useful piece of feedback you could give this trainee?
  - [ ] [SEP formal] What is the most useful feedback that could be provided to this trainee?
  - [ ] [SEP informal] What's the best feedback you could give this trainee?
- D1.3
  - [ ] [SEP original] What should the trainee continue doing, and what should they change?
  - [ ] [SEP humanized] What's working that the trainee should keep doing, and what should they change?
  - [ ] [SEP formal] What should the trainee continue to do, and what should they modify?
  - [ ] [SEP informal] What should the trainee keep doing, and what should they change?
- D1.4
  - [ ] [SEP original] What two coaching points would have the greatest effect on this trainee's development?
  - [ ] [SEP humanized] If you could only give two pieces of coaching, which two would help this trainee the most?
  - [ ] [SEP formal] Which two coaching points would have the greatest effect on this trainee's development?
  - [ ] [SEP informal] What two coaching tips would help this trainee the most?

**Humanized, per dataset**
- [ ] [HUMANIZED] If you sat down with this trainee after watching the video, what would you bring up first?

### D2 Improvement Priority (JIGSAWS D2 now includes D3's corrective action)

**Single wording**
- [ ] [INJECTED] and [CANONICAL] Given the trainee's performance during [a certain video span], among [three feedback points], which area should the trainee improve first and why?
- [x] [IN USE: JIGSAWS] Given the trainee's performance during [a certain video span], among tissue handling, instrument/needle handling, economy of motion, and flow of operation, which area should the trainee improve first, why, and what should the trainee do immediately to address it?
- [x] [IN USE: MB140] Given the trainee's performance during [span], which adverse event should the trainee address first, and why?

**September bank**
- D2.1
  - [ ] [SEP original] Of [three feedback points], which should receive the trainee's attention first, and why?
  - [ ] [SEP humanized] Out of [three feedback points], which one should the trainee tackle first, and why?
  - [ ] [SEP formal] Of [three feedback points], which should receive the trainee's attention first, and for what reason?
  - [ ] [SEP informal] Out of [three feedback points], which should the trainee fix first, and why?
- D2.2
  - [ ] [SEP original] Which of [three feedback points] is currently the greatest barrier to improvement?
  - [ ] [SEP humanized] Which of [three feedback points] is holding this trainee back the most right now?
  - [ ] [SEP formal] Which of [three feedback points] currently constitutes the greatest barrier to improvement?
  - [ ] [SEP informal] Which of [three feedback points] is the biggest thing holding them back?
- D2.3
  - [ ] [SEP original] Where should the trainee begin among [three feedback points]?
  - [ ] [SEP humanized] Where should the trainee start among [three feedback points]?
  - [ ] [SEP formal] *(same as original)*
  - [ ] [SEP informal] Where should the trainee start out of [three feedback points]?
- D2.4
  - [ ] [SEP original] Which of [three feedback points] offers the greatest opportunity for near-term progress?
  - [ ] [SEP humanized] Which of [three feedback points] offers the best shot at quick progress?
  - [ ] [SEP formal] Which of [three feedback points] presents the greatest opportunity for near-term progress?
  - [ ] [SEP informal] Which of [three feedback points] is the easiest win right now?

**Humanized, per dataset:** none for D2. The closest is the D3 one: "Which of [three named feedback points] needs to be addressed first, and what practical change would help?"

### D5 Positive Reinforcement

**Single wording**
- [x] [INJECTED], [CANONICAL] and [IN USE: JIGSAWS] Based on this trainee's performance, what is their strongest technical skill, and why does it matter?
- [x] [IN USE: MB140] Based on the events occurring during [span], which phase went best for the trainee, and why does it matter?

**September bank**
- D5.1
  - [ ] [SEP original] What is the strongest part of this trainee's technical performance, and why is it valuable?
  - [ ] [SEP humanized] What's the strongest part of this trainee's technical performance, and why does it matter?
  - [ ] [SEP formal] What constitutes the strongest aspect of this trainee's technical performance, and why is it valuable?
  - [ ] [SEP informal] What's the trainee's best technical moment here, and why's it worth calling out?
- D5.2
  - [ ] [SEP original] Which skill should the trainee preserve as they work on other areas?
  - [ ] [SEP humanized] What skill should the trainee hold onto while they work on other things?
  - [ ] [SEP formal] Which skill should the trainee retain and preserve while addressing other developmental areas?
  - [ ] [SEP informal] What skill should the trainee not mess with while fixing other stuff?
- D5.3
  - [ ] [SEP original] What aspect of the trainee's performance provides the best foundation for further development?
  - [ ] [SEP humanized] What part of this performance gives the trainee the best foundation to build on?
  - [ ] [SEP formal] What aspect of the trainee's performance provides the most robust foundation for further development?
  - [ ] [SEP informal] What's the best thing the trainee's got going that they can build on?
- D5.4. This one was not run in September, because it presumes progress over time and JIGSAWS has one assessment per trial.
  - [ ] [SEP original] What positive feedback would be most useful for reinforcing this trainee's progress?
  - [ ] [SEP humanized] What positive feedback would help reinforce the progress this trainee's made?
  - [ ] [SEP formal] What positive feedback would be most useful in reinforcing this trainee's progress?
  - [ ] [SEP informal] What positive feedback would help lock in this trainee's progress?

**Humanized, per dataset**
- [ ] [HUMANIZED] What's the strongest part of this trainee's technical performance, and why does it matter?

### Dropped: D3 and D4 (for the record)

- **D3 Corrective Action** [INJECTED]/[CANONICAL]: During [a certain video span], among [three feedback points], which is most urgent, and what should the trainee do immediately to address it? Its corrective-action part now lives in JIGSAWS D2.
  - **September bank:** D3.1–D3.4, each with humanized, formal and informal versions, are in the September bank.
  - **Humanized, per dataset:** "Which of [three named feedback points] needs to be addressed first, and what practical change would help?"
- **D4 Practice Recommendation** [INJECTED]/[CANONICAL]: Among [three feedback points], what simulation drill should the trainee practice next to strengthen the weakest area?
  - **September bank:** D4.1–D4.4, each with humanized, formal and informal versions.
  - **Humanized, per dataset:** "Can you design a focused practice activity for the biggest weakness among [three named feedback points]?"

---

## VTS locate questions (summary)

The shape is from VideoTreeSearch, and Yulu set it for MB140 (10-12) and CholecT50 (10-20).
- **The question** is about a whole recording, and exactly one stretch of it answers it.
- **The answer** is that exact time span plus what the annotation records, in the present tense, with no judgement and no advice.
- **Blind filter:** questions a text-only guess can locate are dropped.

| Dataset | Who wrote it | Kinds (count) | Example question |
|---|---|---|---|
| MultiBypass140 | Eric, 10-20, 432 questions over 137 videos | step 132, phase 132, adverse event 124, most serious bleed 44 (the prompt also lists "first bleed") | "Show the trainee where in this operation the surgeon performs jejunojejunal stapling." / "When in this operation does the gastric pouch creation phase take place?" / "Where in this operation is there a thermal injury described as superficial penetration of vital tissue? Show the trainee when it happens." / "Show the trainee the most serious bleed in this operation." |
| CholecT50 | Eric, 10-20 | an action (instrument, verb, tissue) that happens in a single stretch | Coaching frame: "A trainee wants to see how [X]. Show them when the surgeon does it." |
| JIGSAWS | us, 10-25, 628 questions after the blind filter | phase/throw 18, step 23, step by order 194, step within a stitch 231, error 17, error by order 96, longest delay 26, redo 23 | "When in this recording does the fourth stitch take place?" / "Show the trainee the second time the surgeon pushes the needle through the tissue in this recording." / "Where in this recording is there a needle drop? Show the trainee when it happens." / "Where in this recording does the surgeon lose the most time on a single step, and how much?" |

The JIGSAWS set copies Eric's frames with "operation" changed to "recording". Its review file
is `jigsaws_vts_locate_628_for_review.json` (0 of 628 flagged by the audit).
