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
    r"\b(most urgent|most pressing|first priority|top priority|immediate priority|"
    r"demands? (your )?immediate|requires? (your )?immediate|needs? (your )?immediate|"
    r"immediate attention|address (this )?first|focus first|start (by|with)|begin (by|with)|"
    r"before (anything else|you (worry|tackle|move|address)|addressing|refining|tackling)|"
    r"right now|takes precedence|prioritiz)\b",
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
REAL_PATIENT_ONLY = re.compile(
    r"\b(fascia|wound margin|wound edge|incision|postoperative|post-operative|"
    r"the patient|dermis|epidermis|subcutaneous|peritoneum|artery|vein|organ)\b",
    re.IGNORECASE,
)

# D5: when the top subscore is only 3/5 or below, nothing has actually reached
# "strength" territory. Rather than trying to detect every way an answer might
# oversell (a losing game -- "stands out", "clearest strength", "greatest
# asset"...), require the answer to carry an explicit qualifier about the
# level. Absence of a qualifier is the failure.
LEVEL_QUALIFIER = re.compile(
    r"(relative|not yet|isn't yet|is not yet|still developing|still at a|"
    r"foundational level|baseline (level|competence)|early(-| )stage|"
    r"rather than (advanced|representing advanced)|no (single )?area|none of "
    r"(these|your)|closest to|most developed|room (for|to) (improve|grow)|"
    r"approaching competen|developing proficien)",
    re.IGNORECASE,
)


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
    for d in dirs:
        path = Path(d)
        f = path / "qa_records.jsonl" if path.is_dir() else path
        if not f.exists():
            print(f"!! missing: {f}")
            continue
        recs = [json.loads(l) for l in open(f, encoding="utf-8")]
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
    return 1 if grand_issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["outputs/local_video_d1to5/Suturing"]))
