"""Quality checker for JIGSAWS Template D output.

Checks every rule we've established this session, including the two new ones:
- D3 answers must explicitly name which feedback point is most urgent
  (the question asks a two-part "which is most urgent, and what to do")
- D5 answers must not present a midpoint-or-below subscore as a standout
  strength (system prompt: say so rather than inventing praise)

Usage: python check_template_d.py <dir-with-qa_records.jsonl> [more dirs...]
"""
import json
import re
import sys
from pathlib import Path

GRS_PLAIN_LABELS = {
    "respect_for_tissue": "tissue handling",
    "suture_needle_handling": "instrument/needle handling",
    "time_and_motion": "economy of motion",
    "flow_of_operation": "flow of operation",
    "overall_performance": "overall procedural performance",
    "quality_of_final_product": "quality of the final product",
}

# Phrases that actually *name* an area, as opposed to words that show up
# incidentally in any suturing advice ("needle", "tissue"). Deliberately
# tighter than single keywords: a D3 answer that merely mentions instruments
# in passing has not told the reader which area is most urgent.
LABEL_PHRASES = {
    "tissue handling": [
        "tissue handling", "handling of tissue", "handling the tissue",
        "tissue management", "respect for tissue", "tissue manipulation",
    ],
    "instrument/needle handling": [
        "instrument/needle handling", "needle handling", "instrument handling",
        "needle control", "instrument control", "needle driver control",
        "grip on the needle", "grasp on the needle", "hold on the needle",
        "needle driver", "instrument grip",
    ],
    "economy of motion": [
        "economy of motion", "motion efficiency", "movement efficiency",
        "efficiency of motion", "economical movement", "wasted motion",
        "unnecessary movement", "unnecessary motion", "redundant movement",
    ],
    "flow of operation": [
        "flow of operation", "operative flow", "procedural flow", "workflow",
        "procedural rhythm", "operative rhythm",
    ],
    "overall procedural performance": ["overall procedural performance", "overall performance"],
    "quality of the final product": ["quality of the final product", "final product", "final repair"],
}

# A D3 answer must *declare* a priority, not just discuss technique. The
# question asks two things ("which is most urgent, and what to do"); without
# one of these the first half is unanswered.
URGENCY_DECLARATION = re.compile(
    r"\b(most urgent|most pressing|the priority|a priority|first priority|top priority|"
    r"immediate priority|first correction|addressed? (this )?first|tackle (this )?first|"
    r"demands? (your )?immediate|requires? (your )?immediate|needs? (your )?immediate|"
    r"must be addressed|immediate attention|focus (your |on )?first|start (by|with)|"
    r"begin (by|with)|before (anything else|you (worry|tackle|move|address)|addressing|"
    r"refining|tackling)|right now|takes precedence|prioritiz|"
    # "Tissue handling has to be your immediate focus" -- a priority declared by
    # naming the focus rather than by using the word "priority" or "first".
    r"(?:immediate|primary|main|first|central) focus|"
    r"(?:has|have|needs?) to be your|should be your)\b"
    # The named area sits between the verb and the urgency word, so these need a
    # gap, and the verb is often inflected ("Addressing"). Both orders occur:
    # "Address tissue handling immediately" and "poses the highest immediate risk
    # ... address your tissue handling". Deliberately generous -- three rounds of
    # false positives came from enumerating exact phrasings. This is a structural
    # check, so a missed declaration costs a needless review, while a false
    # positive wastes time chasing a correct answer.
    r"|\b(?:focus|work|start|begin|address|correct|fix|tackl|target|adjust|"
    r"improv|prioritiz|shore)\w*\b[^.;!?]{0,70}"
    r"\b(?:immediat\w*|first|right away|most urgent|highest|greatest|top)\b"
    r"|\b(?:immediat\w*|most urgent|highest|greatest|foremost)\b[^.;!?]{0,70}"
    r"\b(?:focus|work|start|begin|address|correct|fix|tackl|target|adjust|"
    r"improv|prioritiz)\w*\b",
    re.IGNORECASE,
)

NUM_LEAK = re.compile(
    r"\b(?:score[sd]?\s+(?:of\s+)?)?[0-5](?:\.\d+)?\s*(?:out of|/)\s*[0-5]\b"
    r"|\bscore[sd]?\s+(?:of\s+)?[0-5]\b"
)
META_REF = re.compile(
    r"\b(rubric|rating|ratings|assessment|metric|grading|evaluation|scale|your score|the score)\b",
    re.IGNORECASE,
)
BANNED_PHRASE = re.compile(r"\b(the trainee should|prioritize improving)\b", re.IGNORECASE)
FIELD_NAMES = list(GRS_PLAIN_LABELS) + ["grs_total", "grs_subscores"]

# Hard rule 4: general clinical principle is allowed ("rough handling damages
# tissue"), but asserting that THIS trial involved real anatomy or produced a
# patient outcome is not. JIGSAWS is a bench-top exercise with no anatomy
# labels at all. Only terms that are false on a synthetic training pad belong
# here -- deliberately NOT "healing", "bleeding" or "tissue", which are fine in
# general "why this matters" framing and would otherwise drown the signal.
# Hard rule 1: invented mechanical detail. The annotations carry six GRS
# subscores and a skill level -- nothing about grip force, tip control, wrist
# angle, tremor, or where the trainee paused. Coaching may *instruct* on those
# ("keep your grip light") and may explain them in general, but must not
# *assert* them about this trial.
#
# Detecting that needs the assertion, not the vocabulary: matching "grip" alone
# would flag every legitimate instruction. So this pairs a mechanic term with a
# present-tense indicative construction -- "your grip pressure is too high",
# "the needle driver feels unsteady", "there are noticeable pauses".
# Physical specifics the annotations never record.
# Grip/force is deliberately absent here, matching HABIT below: respect_for_tissue
# measures force exerted on tissue, so force language restates a weak label. It
# only becomes fabrication when that score is HIGH -- see FORCE_CLAIM, which is
# checked against the subscore value rather than unconditionally. Leaving grip in
# this list false-positived a legitimate general principle ("Excessive grip
# strength or hesitant movements ... compromise your ability to ...").
MECHANIC = (
    r"tip (?:control|placement)|"
    r"wrist (?:angle|position|control)|instrument (?:control|steadiness)|"
    r"needle driver|tremor|hand movements?|instrument travel"
)
# A state judgement. Fabrication is a mechanic *plus* one of these asserted as
# current fact. Without a state word, "your economy of motion is at a
# foundational level" (the required level statement) and "control of the needle
# driver is the foundation for knot tying" (a general truth) both pass, as they
# should.
# "hesitant" is absent: the instrument-handling anchor licenses "tentative"
# moves, so hesitancy is score-conditional, not a state nothing supports.
STATE = (
    r"unsteady|erratic|shak\w+|jerk\w+|imprecise|sloppy|clumsy|unstable|"
    r"inconsistent|rough|poor|inadequate|weak|too\s+(?:high|tight|"
    r"loose|fast|slow|much|heavy)|excessive|noticeable|visible|frequent"
)
ASSERTED_MECHANIC = re.compile(
    # "your grip pressure is too high", "the needle driver feels unsteady"
    rf"\b(?:your|the)\s+(?:\w+\s+){{0,2}}(?:{MECHANIC})\s+"
    rf"(?:is|are|was|were|feels?|seems?|looks?|remains?|appears?|becomes?)\s+"
    rf"(?:\w+\s+){{0,2}}(?:{STATE})"
    # reverse order: "erratic wrist control", "noticeable pauses"
    rf"|\b(?:{STATE})\s+(?:\w+\s+){{0,2}}(?:{MECHANIC})"
    # "your current fumbling", "your visible hesitation". Restricted to observed
    # behaviour nouns -- "your current stage/level/performance/skill" are all
    # legitimate ways to refer to where the trainee is.
    # fumbling, hesitation and clumsiness are NOT here -- the instrument-handling
    # anchor licenses "tentative or awkward moves", so they are score-conditional.
    # These have no anchor in any element.
    rf"|\byour\s+(?:current|apparent|evident|visible|obvious)\s+"
    rf"(?:tremor|shakiness|jerkiness|unsteadiness|over-?adjust\w*|"
    rf"over-?correct\w*)"
    # "your hands hesitate / shake / drift / over-adjust"
    rf"|\byour\s+hands?\s+(?:\w+\s+)?(?:hesitat|shak|drift|wander|jerk|tremble|"
    rf"over-?adjust|over-?correct)\w*"
    # "you are jerking the needle" -- present continuous claim about a mechanic no
    # anchor covers. Force, gripping, pausing, hesitating and fumbling verbs are
    # deliberately absent: each is some element's own anchor vocabulary, so they
    # are score-conditional via LICENSED_BY_LOW_SCORE rather than banned here.
    # Leaving "apply" in this branch flagged "you are applying too much pressure"
    # at respect_for_tissue 1/5, where the anchor is literally unnecessary force.
    rf"|\byou\s+are\s+\w*(?:jerk|overshoot|undershoot|trembl|shak)\w*ing\b",
    re.IGNORECASE,
)

# A mechanic phrase in one of these frames is not a claim about this trial:
#   goal state    "drill until the motion feels automatic"
#   conditional   "when your movements are tight, you conserve stamina"
#   contrastive   "use your wrist instead of excessive hand movement"
#   general rule  "inefficient workflow tends to make wrist control erratic"
# Only an unframed present-tense claim asserts something happened.
NON_ASSERTIVE_FRAME = re.compile(
    r"\b(until|once|when|whenever|if|unless|instead of|rather than|so that|"
    r"in order to|as soon as|before|after|tends? to|often|typically|usually|"
    r"generally|always|signals?|indicates?|suggests?|can|could|would|will|may|"
    r"might)\b",
    re.IGNORECASE,
)
# Narrower set for looking *ahead*. A modal or generality marker after the
# phrase makes it a general rule ("inconsistent grip will destabilize..."). A
# contrastive like "instead of" must NOT appear here: it happily follows a real
# assertion ("your hands hesitate ... instead of following a direct path").
GENERAL_AHEAD = re.compile(
    r"\b(will|would|can|could|may|might|tends? to|often|typically|usually|generally)\b",
    re.IGNORECASE,
)


def find_asserted_mechanic(answer: str) -> str | None:
    """Mechanical claim about this trial, or None.

    Checks the words immediately before each candidate: a goal, conditional,
    contrastive or general-rule frame means the phrase describes what should
    happen or what happens in general, not what this trainee did.
    """
    for match in ASSERTED_MECHANIC.finditer(answer):
        lead = answer[max(0, match.start() - 70) : match.start()]
        # Only the tail of the preceding clause matters -- a framing word from
        # an earlier sentence is unrelated.
        clause = re.split(r"[.;!?]", lead)[-1]
        if NON_ASSERTIVE_FRAME.search(clause):
            continue
        # The frame can also follow: "excessive grip strength ... *will*
        # damage your materials" is a general rule, not a claim about this
        # trial. Only a few words ahead, so a modal later in the sentence
        # cannot excuse an assertion made earlier in it.
        ahead = " ".join(answer[match.end():].split()[:8])
        if GENERAL_AHEAD.search(ahead):
            continue
        return match.group(0)
    return None


# Hard rule 1, presupposition form. The checks above look for a mechanic
# *asserted* as fact. This one catches the same fabrication carried by a verb:
# telling the trainee to reduce, stop, or shift away from a named mechanic
# asserts they are doing it. "The priority is shifting away from relying on a
# heavy grip" claims a heavy grip exactly as much as "your grip is too heavy",
# and one subscore for a skill never records which mechanic produced it.
#
# Deliberately NOT excused by NON_ASSERTIVE_FRAME: a presupposition survives
# being embedded in a conditional or a goal frame. "Once you stop squeezing so
# hard" still asserts the squeezing. That is the whole point of the pattern, so
# it gets its own matcher rather than joining ASSERTED_MECHANIC.
REDUCTION = (
    r"shift(?:ing)?\s+away\s+from|mov(?:e|ing)\s+away\s+from|"
    r"get(?:ting)?\s+away\s+from|wean(?:ing)?\s+(?:yourself\s+)?off|"
    r"stop|cease|quit|ease\s+off(?:\s+on)?|let\s+up\s+on|refrain\s+from|"
    r"cut\s+(?:down|back)\s+on|break\s+the\s+habit\s+of|dial\s+back|"
    r"back\s+off\s+on|curb|abandon|reduc\w+|lessen|"
    # "this repetition will quiet the tremors in your wrist" -- a cure verb
    # asserts the symptom exists just as plainly as "reduce" does.
    r"quiet|eliminat\w+|smooth\s+out|iron\s+out|fix|correct"
)
# Mechanics that no GRS element measures, so telling the trainee to do less of
# one invents a cause for a number.
#
# The dividing line is each element's own definition. JIGSAWS uses a modified
# GRS adapted from OSATS, where "respect for tissue" measures force exerted on
# tissue (lowest band: unnecessary force, or damage from inappropriate use of
# instruments), "suture/needle handling" measures control while tying, and
# "time and motion" measures fluency and efficiency of movement. So force, grip
# pressure, instrument control and wasted motion are all vocabulary the labels
# themselves supply -- "apply only the minimum force needed" and "reduce
# unnecessary instrument travel" restate a weak score instead of inventing one.
# Those are deliberately absent from this set.
#
# What remains are specifics nothing in the input records: wrist angle, tip
# control, tremor, where the trainee paused. Those are the fabrications actually
# observed in output ("your wrist control becomes erratic", "your current
# fumbling"), and they stay flagged.
# Note what is NOT here, each checked against a real answer and its subscores:
#   grip / force / pressure  -- respect_for_tissue measures force on tissue
#   finger tension           -- that is grip force under another name
#   tearing, damage          -- the lowest tissue band's anchor names damage
#   wasted motion / travel   -- time_and_motion measures movement efficiency
#   instrument control       -- suture_needle_handling measures control
# Wrist and non-dominant-hand *instructions* are fine too ("keep your wrist
# relaxed and let the pivot point guide the trajectory") -- 15 of 18 wrist
# mentions in the reviewed batch were legitimate technique advice, so matching
# the bare word would be wrong. Only posture asserted as a current habit lands.
HABIT = (
    r"tip (?:control|placement)|wrist (?:angle|position|control)|"
    # Hesitation, fumbling and pauses are NOT here: the OSATS anchors license
    # "tentative" moves for instrument handling and "stopped operating" for flow
    # of operation, so they are score-conditional (LICENSED_BY_LOW_SCORE) rather
    # than banned. What remains has no anchor in any element.
    # "tremors" needs the optional s -- the trailing \b rejects a bare "tremor".
    r"tremors?|jerk\w+|shakiness|unsteadiness|"
    r"(?:awkward|compensatory|poor|bad)(?:,?\s+\w+)?\s+(?:hand |body |arm )?"
    r"(?:position|posture)s?"
)
PRESUPPOSED_HABIT = re.compile(
    # "reduce your grip pressure", "stop squeezing", "ease off on the pressure"
    rf"\b(?:{REDUCTION})\s+(?:\w+\s+){{0,2}}(?:{HABIT})\b"
    # "relying on a heavy grip" -- the object of "shifting away from" above, and
    # ungrounded on its own regardless of what introduces it.
    rf"|\brely(?:ing)?\s+on\s+(?:\w+\s+){{0,2}}(?:{HABIT})\b"
    rf"|\byour\s+reliance\s+on\b",
    re.IGNORECASE,
)


# A negation or goal frame flips the meaning: "without unnecessary tension" and
# "prevents unnecessary pauses" describe the state to reach, not one observed. An
# imperative can too -- "pause between each step to map the next path" recommends a
# pause rather than complaining about one. Without this, every one of these read as
# an accusation, which false-positived all four score flags in the v3 batch.
GOAL_OR_NEGATED = re.compile(
    r"\b(without|free (?:of|from)|prevent\w*|avoid\w*|minimi[sz]\w+|"
    r"eliminat\w+ the need|instead of|rather than|no longer|so that you do not|"
    r"keeps?|maintain\w*|preserv\w*)\b",
    re.IGNORECASE,
)
# The vocabulary as a deliberate instruction, not a criticism. "To correct this
# immediately, pause between each step" prescribes a pause; the giveaway is that
# the word sits in imperative position -- opening its clause, i.e. preceded by
# nothing or by a comma.
IMPERATIVE_VERB = re.compile(r"^(?:pause|stop|halt|slow)\b", re.IGNORECASE)

# A present-tense consequence verb immediately after the mechanic, which makes the
# mechanic the subject of a general rule rather than an attributed fault.
GENERAL_CONSEQUENCE = re.compile(
    r"\s*(?:during|in|at|on|when|while)?\s*[\w\s]{0,30}?\b"
    r"(creates?|caus\w+|leads?|caus\w+|disrupts?|compromis\w+|increas\w+|reduc\w+|"
    r"makes?|produc\w+|results?|risks?|forces?|prevents?|degrad\w+)\b",
    re.IGNORECASE,
)


def _in_imperative_position(clause: str, matched: str) -> bool:
    lead = clause.strip()
    return (not lead or lead.endswith(",")) and bool(IMPERATIVE_VERB.match(matched))


# At 4 the trainee sits between the score-3 anchor ("competent but occasionally
# stiff or awkward", "efficient but some unnecessary moves") and the score-5 one
# ("no awkwardness", "maximum efficiency"), so acknowledging a *minor residual* is
# correct interpolation, not contradiction. Only unhedged criticism is wrong at 4.
# At 5 the anchor denies the trait outright, so any degree of it contradicts.
HEDGED = re.compile(
    r"\b(minor|occasional|occasionally|slight|slightly|residual|some|a few|"
    r"mild|small|remaining|still|nearly|almost|largely|mostly|generally)\b",
    re.IGNORECASE,
)


def find_score_contradiction(
    answer: str, pattern: re.Pattern, hedge_ok: bool = False
) -> str | None:
    """Anchor vocabulary asserted against a high score, or None.

    Skips matches sitting in a goal, negation, or recommendation frame -- those
    name the target state or prescribe an action rather than reporting a fault.
    With hedge_ok (score 4), also skips hedged mentions, which interpolate
    correctly between the score-3 and score-5 anchors.
    """
    for match in pattern.finditer(answer):
        lead = answer[max(0, match.start() - 80) : match.start()]
        clause = re.split(r"[.;!?]", lead)[-1]
        if GOAL_OR_NEGATED.search(clause):
            continue
        if _in_imperative_position(clause, match.group(0)):
            continue
        ahead = " ".join(answer[match.end():].split()[:6])
        if GENERAL_AHEAD.search(ahead):
            continue
        if hedge_ok and HEDGED.search(clause + " " + ahead):
            continue
        # A general principle states the mechanic as the subject of a consequence
        # with no possessive attaching it to this trainee: "stiffness or excessive
        # grip tension creates mechanical resistance" describes the mechanic in
        # general, whereas "your excessive grip tension creates..." asserts it.
        if not re.search(r"\b(your|you)\b", clause[-40:], re.IGNORECASE) and GENERAL_CONSEQUENCE.match(ahead):
            continue
        return match.group(0)
    return None


REAL_PATIENT_ONLY = re.compile(
    r"\b(fascia|wound margin|wound edge|incision|postoperative|post-operative|"
    r"the patient|dermis|epidermis|subcutaneous|peritoneum|artery|vein|organ|"
    # bench-top pads have no perfusion, so no ischaemic or healing outcome
    r"ischemi\w+|ischaemi\w+|necrosis|necrotic|perfusion|blood supply|"
    r"granulation|dehiscence)\b",
    re.IGNORECASE,
)

# D5: when the top subscore is only 3/5 or below, nothing has actually reached
# "strength" territory. Rather than trying to detect every way an answer might
# oversell (a losing game -- "stands out", "clearest strength", "greatest
# asset"...), require the answer to carry an explicit qualifier about the
# level. Absence of a qualifier is the failure.
LEVEL_QUALIFIER = re.compile(
    r"(relative|not yet|isn't yet|is not yet|still developing|still at a|"
    r"foundational level|baseline (level|competence)|early[- ]?\w*\s?stage|"
    r"rather than (advanced|representing advanced)|no (single )?area|none of "
    r"(these|your)|closest to|most developed|room (for|to) (improve|grow)|"
    r"approaching competen|developing proficien"
    # Real qualifiers the enumeration missed. The level word varies (basic /
    # foundational / baseline / developing) and pairs with either "level" or
    # "stage"; and the thing that has not reached strength may be called an area,
    # technique or skill.
    r"|(basic|foundational|baseline|developing|elementary) (level|stage|tier)"
    r"|(rather|other) than a (true |real |developed )?strength"
    r"|no single (area|technique|skill|domain)"
    r"|has(n't| not) yet reached|yet to reach|short of a (true )?strength"
    r"|least weak|strongest of (the |these )?(weak|low)"
    # Negated-strength constructions. Enumerating exact phrasings kept missing
    # real ones -- "introductory level", "Nothing has reached a clear strength
    # yet", "rather than demonstrating actual strength" were all qualifiers the
    # checker called missing. These match on the structure instead: a negator
    # anywhere before "strength" in the same clause, or a level word before
    # "level/stage", whatever adjective or verb sits between.
    r"|(has|have|had)(n't| not)[^.;!?]{0,50}\b(yet|strength)\b"
    r"|\b(nothing|neither|none|not one|no single|no one)\b[^.;!?]{0,70}\b(strength|strong)"
    r"|\b(rather|other) than\b[^.;!?]{0,50}\bstrength"
    r"|\b(introductory|entry|beginner|novice|early|emerging|nascent)[- ]?\w*\s+"
    r"(level|stage|tier|point)"
    r"|no (clear|true|real|established) strength|furthest along|most reliab\w+"
    r")",
    re.IGNORECASE,
)


# Vocabulary each GRS element licenses, taken from the OSATS score-1 anchors that
# JIGSAWS' modified GRS derives from (JIGSAWS ships only names and numbers):
#
#   Respect for Tissue   "Frequently used unnecessary force on tissue or caused
#                         damage by inappropriate use of instruments"
#   Time and Motion      "Many unnecessary moves"
#   Instrument Handling  "Repeatedly makes tentative or awkward moves with
#                         instruments"          -> JIGSAWS suture_needle_handling
#   Flow of Operation    "Frequently stopped operating and seemed unsure of next
#                         move"
#
# So this vocabulary is a restatement of a LOW score, not an invented cause. It
# becomes fabrication only when the corresponding score is high -- at 4-5 the
# anchor records the opposite, and "there are noticeable pauses between steps"
# then has nothing behind it. Hence each pattern is checked against its own
# subscore rather than banned outright.
#
# Caveat: these are the OSATS anchors. JIGSAWS' own wording for its modified
# elements (notably "Suture/needle handling") is not published in the dataset, so
# that one row is inferred from its OSATS ancestor.
LICENSED_BY_LOW_SCORE = [
    (
        "respect_for_tissue",
        re.compile(
            r"\b(?:excessive|unnecessary|too much|heavy|forceful|aggressive)\s+"
            r"(?:\w+\s+){0,2}(?:force|pressure|grip|grasp|tension)"
            # reverse order too: "your grip is too heavy", "the pressure was excessive"
            r"|\b(?:your|the)\s+(?:\w+\s+){0,2}(?:grip|grasp|force|pressure|tension)"
            r"\s+(?:is|are|was|were|becomes?|feels?|seems?)\s+(?:\w+\s+){0,2}"
            r"(?:too\s+\w+|excessive|heavy|tight|forceful|aggressive)"
            r"|\b(?:relax|lighten|reduc\w+|ease\s+off\s+on)\s+(?:your|the)\s+"
            r"(?:\w+\s+){0,2}(?:grip|grasp|force|pressure)"
            r"|\bcrush\w*|\btear\w*|\bdamag\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "time_and_motion",
        re.compile(
            r"\b(?:unnecessary|redundant|wasted|extra|superfluous)\s+"
            r"(?:\w+\s+){0,2}(?:moves?|movements?|motion|travel|repositioning)"
            r"|\bbacktrack\w*|\bsweeping arcs?",
            re.IGNORECASE,
        ),
    ),
    (
        "suture_needle_handling",
        re.compile(
            r"\btentative|\bawkward|\bstiff\b|\bhesitan\w+|\bhesitat\w+|"
            r"\bfumbl\w+|\bclumsi\w+|\bclumsy\b",
            re.IGNORECASE,
        ),
    ),
    (
        "flow_of_operation",
        re.compile(
            r"\bpaus\w+|\bstopp\w+|\bunsure of (?:the )?next|\bhalt\w*|"
            r"\bstall\w+|\binterrupt\w+",
            re.IGNORECASE,
        ),
    ),
]


def three_lowest(subscores):
    order = list(GRS_PLAIN_LABELS)
    ranked = sorted(order, key=lambda n: (subscores[n], order.index(n)))
    return [GRS_PLAIN_LABELS[n] for n in ranked[:3]]


def names_an_area(answer, candidate_labels):
    """Does the answer clearly point at one of the candidate areas?"""
    low = answer.lower()
    hits = []
    for label in candidate_labels:
        for phrase in LABEL_PHRASES[label]:
            if phrase in low:
                hits.append(label)
                break
    return hits


def check_record(r):
    issues = []
    if r.get("validation_status") not in ("valid", "valid_mock"):
        issues.append(f"REJECTED: {r.get('validation_error')}")
        return issues
    qa = r["qa"][0]
    answer, question = qa["answer"], qa["question"]
    tid = r["template_id"]
    sa = r["source_annotation"]

    if NUM_LEAK.search(answer):
        issues.append(f"numeric leak: {NUM_LEAK.search(answer).group(0)!r}")
    for fn in FIELD_NAMES:
        if fn in answer:
            issues.append(f"field-name leak: {fn}")
    if META_REF.search(answer):
        issues.append(f"meta-reference: {META_REF.search(answer).group(0)!r}")
    if BANNED_PHRASE.search(answer):
        issues.append(f"banned phrase: {BANNED_PHRASE.search(answer).group(0)!r}")
    if "these three feedback points" in question or "the three feedback points" in question:
        issues.append("question not self-contained")
    anatomy = REAL_PATIENT_ONLY.search(answer)
    if anatomy:
        issues.append(
            f"real-patient anatomy/outcome asserted for a bench-top trial: {anatomy.group(0)!r}"
        )
    fabricated = find_asserted_mechanic(answer)
    if fabricated:
        issues.append(
            f"asserts a mechanical detail the annotations do not contain: "
            f"{fabricated!r}"
        )
    # Anchor vocabulary used against a score that does not support it. At 4-5 the
    # anchor records the opposite of what the answer is coaching.
    scores = sa.get("grs_subscores") or {}
    for field, pattern in LICENSED_BY_LOW_SCORE:
        score = scores.get(field)
        if score is None or score < 4:
            continue
        hit = find_score_contradiction(answer, pattern, hedge_ok=(score == 4))
        if hit:
            issues.append(
                f"coaches on {GRS_PLAIN_LABELS.get(field, field)} but {field} is "
                f"{score}/5, which records the opposite: {hit!r}"
            )
    presupposed = PRESUPPOSED_HABIT.search(answer)
    if presupposed:
        issues.append(
            f"presupposes a habit the annotations do not record -- give a target "
            f"to aim for, not one to stop: {presupposed.group(0)!r}"
        )

    subs = sa.get("grs_subscores")

    # NEW CHECK 1 -- D3 asks a two-part question ("which is most urgent, and
    # what to do"). Both halves must be answered: the area has to be named,
    # and it has to be flagged as the priority.
    if tid == "D3" and subs:
        named = names_an_area(answer, three_lowest(subs))
        declared = URGENCY_DECLARATION.search(answer)
        if not named:
            issues.append("D3 never names which of the three areas it means")
        if not declared:
            issues.append("D3 gives no priority declaration (question asks which is MOST urgent)")

    # NEW CHECK 2 -- D5 must not present a midpoint-or-below top score as a
    # developed strength without qualifying the level.
    if tid == "D5" and subs:
        top = max(subs.values())
        if top <= 3 and not LEVEL_QUALIFIER.search(answer):
            issues.append(
                f"D5 top subscore is only {top}/5 but the answer never qualifies "
                f"the level (reads as praise for a developed strength)"
            )
    return issues


def main(dirs):
    grand_total = grand_issues = 0
    unusable = 0
    for d in dirs:
        path = Path(d)
        f = path / "qa_records.jsonl" if path.is_dir() else path
        if not f.exists():
            print(f"!! missing: {f}")
            unusable += 1
            continue
        recs = [json.loads(l) for l in open(f, encoding="utf-8")]
        if not recs:
            # A pass over zero records is not a pass. The runner opens the file
            # with "w" before generating and Python holds the per-record writes
            # in its buffer until the handle closes, so an in-progress run looks
            # exactly like this -- reporting it clean invites reading "0 issues"
            # as a result when nothing was checked.
            print(f"\n=== {f} (0 records) ===")
            print("  !! empty -- nothing was checked (run still in progress, or it wrote nothing)")
            unusable += 1
            continue
        n_issues = 0
        print(f"\n=== {f} ({len(recs)} records) ===")
        for r in recs:
            issues = check_record(r)
            if issues:
                n_issues += len(issues)
                print(f"  {r['template_id']:3s} {r['trial_id']:22s} -> {issues}")
        if n_issues == 0:
            print("  all clean")
        grand_total += len(recs)
        grand_issues += n_issues
    print(f"\n{'='*70}")
    print(f"TOTAL: {grand_total} records, {grand_issues} issues")
    if unusable:
        print(f"{unusable} input(s) missing or empty -- their records were NOT checked")
    return 1 if grand_issues or unusable else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["outputs/local_video_d1to5/Suturing"]))
