# Template questions, one per type (2026-11-06)

Each type has one question. It is the wording that has been injected into the system prompt
and used in our runs; for D, the wording the coaching runs ask. Brackets are filled per
item: [t] is a timestamp, [action] an annotated action, and [clip] or
[a certain video span] the video span.

## Type A: Basic Perception

| Type | Question |
|---|---|
| A1 Anatomy Identification | At timestamp [t], which anatomical structure or region is most relevant to the current action? |
| A2 Procedural/Order Identification | What phase of the procedure is shown at [t]? |
| A3 Comparison | Compare clip A and clip B by expert vs. trainee. Which one shows better technique? |
| A4 Timestamp | At what timestamp does the trainee first lose safe visualization? |

Type A has never been injected or generated, so these come from `template-questions-A-D.md`.
Each template there lists several questions; each pick is the one that matches the template's
name. Both A3 questions are comparisons. The clip comparison was picked because JIGSAWS's
expert and novice trials could be paired for it, while no dataset records the feedback event
the other one needs. Most of Type A needs the model to see the frame.

## Type B: Safety and Danger Detection

| Type | Question |
|---|---|
| B1 Dangerous Frame Critique | At timestamp [t], identify the most urgent patient-safety concern. |
| B2 Is It Safe To Proceed? | Based on the frame at [t], is it medically safe to proceed with [action]? |
| B3 Risk Ranking | Rank the intraoperative risks from highest to lowest clinical priority. |
| B4 Near-Miss Detection | Does this clip show a near-miss event that could have resulted in patient harm? |
| B5 Stop Point | At which timestamp should a supervisor pause the trainee to prevent possible patient harm? |
| B6 Cause Of Error | At which timestamp should a supervisor pause the trainee for coaching, and why? |
| B7 CVS Safety Check | Based on the frame at [t], is it medically safe to proceed with clipping or cutting the cystic duct or artery? |

## Type C: Skill Assessment

| Type | Question |
|---|---|
| C1 Tissue Handling Score | How would you rate the trainee's tissue handling skills in [clip] from 1-5? Provide: score, visible evidence, potential tissue injury risk, and one concrete improvement. |
| C2 Instrument Handling Score | How would you rate the instrument handling in [clip] from 1-5? Provide: score, key evidence, unsafe movement if present, and corrective suggestion. |
| C3 Economy Of Motion | Does the trainee use efficient motion in this clip? Identify unnecessary movements and suggest a more efficient alternative. |
| C4 Bimanual Dexterity | How well does the trainee coordinate both hands? Identify whether one hand is passive, obstructive, or not optimizing exposure. |
| C5 Depth Perception / Targeting | Does the trainee accurately direct the instrument tip to the intended plane or target? |
| C6 Flow Of Operation | Does the trainee demonstrate smooth procedural flow and anticipation of the next operative step? |
| C7 Autonomy Level | Based on the clip, what level of supervision would the trainee likely require? |

C4 and C5 are not generated for JIGSAWS, which has no label for either skill.

## Type D: Coaching Feedback

| Type | Question |
|---|---|
| D1 Overall Feedback | If you were the supervising surgeon, based only on the events occurring during [a certain video span], what feedback would you give on the trainee's tissue handling, instrument/needle handling, economy of motion, and flow of operation? |
| D2 Priority and Corrective Action | Given the trainee's performance during [a certain video span], among tissue handling, instrument/needle handling, economy of motion, and flow of operation, which area should the trainee improve first, why, and what should the trainee do immediately to address it? |
| D5 Positive Reinforcement | Based on this trainee's performance, what is their strongest technical skill, and why does it matter? |

MultiBypass140 has adverse events instead of skill ratings, so it asks adapted versions:

- **D1:** If you were the supervising surgeon, based only on the events occurring during [a certain video span], what feedback would you give on the trainee's performance?
- **D2:** Given the trainee's performance during [a certain video span], which adverse event should the trainee address first, and why?
- **D5:** Based on the events occurring during [a certain video span], which phase went best for the trainee, and why does it matter?

D3 and D4 are dropped. D3's corrective action is now part of D2.

## VTS locate questions

Each question is about a whole recording and has exactly one answer: the time span, plus
what the annotation records about it.

| Kind | Question |
|---|---|
| Phase | When in this operation does the gastric pouch creation phase take place? |
| Step | Show the trainee where in this operation the surgeon performs jejunojejunal stapling. |
| Adverse event or error | Where in this operation is there a thermal injury described as superficial penetration of vital tissue? Show the trainee when it happens. |
| Most serious | Show the trainee the most serious bleed in this operation. |
| By order (JIGSAWS) | Show the trainee the second time the surgeon pushes the needle through the tissue in this recording. |
| Redo (JIGSAWS) | Where in this recording does the surgeon return to pulling suture with the left hand right after using the right hand to help tighten the suture? Show the trainee when it happens. |
| Action (CholecT50) | A trainee wants to see how the cystic artery is clipped. Show them when the surgeon does it. |
