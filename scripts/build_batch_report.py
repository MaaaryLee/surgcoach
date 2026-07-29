"""Render a generated QA batch as a single self-contained HTML page.

    python3 scripts/build_batch_report.py <batch-dir-or-jsonl> [-o out.html]

Every count and every quality claim on the page is computed from the records, and
the issue list comes from check_template_d itself rather than from a list written
by hand. That is deliberate: the previous version of this generator hardcoded
"0 rejected - 0 retries" and "all 75 records also pass check_template_d.py" into
the template, so the page kept asserting both after they had stopped being true.
A page that reports on data must not be able to disagree with it.

Rejected records are rendered in place as "no record produced" rather than
skipped, so a batch of 75 with one failure cannot read as 75 usable records.
"""
import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_template_d import check_record

DOMAINS = [
    ("respect_for_tissue", "Tissue handling"),
    ("suture_needle_handling", "Needle handling"),
    ("time_and_motion", "Economy of motion"),
    ("flow_of_operation", "Flow of operation"),
    ("overall_performance", "Overall"),
    ("quality_of_final_product", "Final product"),
]
TEMPLATE_NAMES = {
    "C1": "Tissue handling",
    "C2": "Instrument & needle handling",
    "C3": "Economy of motion",
    "C6": "Flow of operation",
    "C7": "Autonomy level",
    "D1": "Coaching feedback",
    "D2": "Feedback with priority",
    "D3": "Corrective action",
    "D4": "Practice recommendation",
    "D5": "Positive reinforcement",
}
SKILL = {"N": "Novice", "I": "Intermediate", "E": "Expert"}
TASK_ORDER = ["Suturing", "Knot Tying", "Needle Passing"]

CSS = """
  :root {
    --ground: #f6f7f9; --surface: #ffffff; --ink: #15181e; --muted: #5c6675;
    --accent: #2c5f72; --accent-soft: #e8eef1; --line: #e1e5ea; --line-strong: #c8d0d8;
    --s1: #b03a2e; --s2: #c07a2c; --s3: #a8942f; --s4: #5b8a48; --s5: #2f6b4f;
    --serif: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
    --sans: "Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    --mono: ui-monospace, "Cascadia Mono", Consolas, "Liberation Mono", monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --ground: #101318; --surface: #161a20; --ink: #e6e9ed; --muted: #98a2b0;
      --accent: #6fb3c4; --accent-soft: #1a2a31; --line: #262c34; --line-strong: #39424d;
      --s1: #d9705f; --s2: #d19a4e; --s3: #c4b45a; --s4: #86b06e; --s5: #5e9d7c;
    }
  }
  :root[data-theme="dark"] {
    --ground: #101318; --surface: #161a20; --ink: #e6e9ed; --muted: #98a2b0;
    --accent: #6fb3c4; --accent-soft: #1a2a31; --line: #262c34; --line-strong: #39424d;
    --s1: #d9705f; --s2: #d19a4e; --s3: #c4b45a; --s4: #86b06e; --s5: #5e9d7c;
  }
  :root[data-theme="light"] {
    --ground: #f6f7f9; --surface: #ffffff; --ink: #15181e; --muted: #5c6675;
    --accent: #2c5f72; --accent-soft: #e8eef1; --line: #e1e5ea; --line-strong: #c8d0d8;
    --s1: #b03a2e; --s2: #c07a2c; --s3: #a8942f; --s4: #5b8a48; --s5: #2f6b4f;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body { background: var(--ground); color: var(--ink); font-family: var(--sans);
    line-height: 1.55; -webkit-font-smoothing: antialiased; }
  .wrap { max-width: 62rem; margin: 0 auto; padding: 2.5rem 1.5rem 6rem; }

  header.top { display: flex; flex-direction: column; gap: .5rem; padding-bottom: 1.1rem;
    border-bottom: 2px solid var(--line-strong); margin-bottom: 1.6rem; }
  header.top h1 { font-family: var(--serif); font-size: clamp(1.5rem, 3.6vw, 2.1rem);
    font-weight: 600; margin: 0; letter-spacing: -.01em; text-wrap: balance; }
  .runline { font-family: var(--mono); font-size: .74rem; color: var(--muted);
    display: flex; flex-wrap: wrap; gap: .35rem 1.1rem; font-variant-numeric: tabular-nums; }
  .runline b { color: var(--accent); font-weight: 600; }

  .lede { font-family: var(--serif); font-size: 1.02rem; max-width: 64ch; margin: 0 0 2.2rem; }
  .lede em { color: var(--muted); font-style: italic; }

  .known { border-left: 3px solid var(--s2); background: var(--surface);
    padding: .9rem 1.1rem; margin: 0 0 2.2rem; border-radius: 0 3px 3px 0;
    border-top: 1px solid var(--line); border-right: 1px solid var(--line);
    border-bottom: 1px solid var(--line); }
  .known.clean { border-left-color: var(--s4); }
  .known p { margin: 0 0 .6rem; font-size: .88rem; max-width: 72ch; }
  .known p:last-child { margin-bottom: 0; }
  .known ul { margin: 0 0 .7rem; padding-left: 1.1rem; }
  .known li { font-size: .86rem; margin-bottom: .3rem; max-width: 72ch; }
  .known code { font-family: var(--mono); font-size: .82em; color: var(--accent); }

  h2 { font-family: var(--sans); font-size: .8rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .13em; color: var(--accent); margin: 3rem 0 1rem; padding-bottom: .45rem;
    border-bottom: 1px solid var(--line-strong); }
  .task:first-of-type h2 { margin-top: 0; }

  .matrix-wrap { overflow-x: auto; margin-bottom: 1rem; border: 1px solid var(--line);
    border-radius: 3px; background: var(--surface); }
  table { border-collapse: collapse; width: 100%; font-size: .82rem; }
  th { font-size: .68rem; text-transform: uppercase; letter-spacing: .09em; color: var(--muted);
    font-weight: 600; text-align: left; padding: .6rem .8rem;
    border-bottom: 1px solid var(--line-strong); white-space: nowrap; }
  td { padding: .5rem .8rem; border-bottom: 1px solid var(--line); vertical-align: baseline; }
  tr:last-child td { border-bottom: 0; }
  .c-trial { font-family: var(--mono); font-size: .76rem; }
  .c-num { font-family: var(--mono); font-size: .76rem; font-variant-numeric: tabular-nums;
    white-space: nowrap; }
  .of { color: var(--muted); }
  .c-weak { color: var(--muted); font-size: .78rem; }

  .trial { margin-bottom: 2.6rem; }
  .trial-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: .3rem .8rem;
    margin-bottom: .55rem; }
  .trial-head h3 { font-family: var(--mono); font-size: .92rem; font-weight: 600; margin: 0;
    letter-spacing: -.01em; }
  .trial-head .meta { font-family: var(--mono); font-size: .72rem; color: var(--muted);
    font-variant-numeric: tabular-nums; }

  .profile { display: grid; grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr));
    gap: .3rem .9rem; padding: .7rem .85rem; margin-bottom: 1.1rem; background: var(--surface);
    border: 1px solid var(--line); border-radius: 3px; }
  .dom { display: flex; align-items: center; justify-content: space-between; gap: .5rem;
    font-size: .74rem; }
  .dom-name { color: var(--muted); }
  .score { display: inline-flex; align-items: center; gap: .4rem; }
  .score-bar { display: block; width: 2.4rem; height: 4px; border-radius: 2px;
    background: var(--line-strong); position: relative; overflow: hidden; }
  .score-bar::after { content: ""; position: absolute; inset: 0 auto 0 0; border-radius: 2px; }
  .score-num { font-family: var(--mono); font-size: .72rem; font-variant-numeric: tabular-nums;
    min-width: .7rem; text-align: right; }
  .s1 .score-bar::after { width: 20%; background: var(--s1); }
  .s2 .score-bar::after { width: 40%; background: var(--s2); }
  .s3 .score-bar::after { width: 60%; background: var(--s3); }
  .s4 .score-bar::after { width: 80%; background: var(--s4); }
  .s5 .score-bar::after { width: 100%; background: var(--s5); }
  .s1 .score-num { color: var(--s1); } .s2 .score-num { color: var(--s2); }
  .s3 .score-num { color: var(--s3); } .s4 .score-num { color: var(--s4); }
  .s5 .score-num { color: var(--s5); }

  .rec { display: grid; grid-template-columns: 7.5rem 1fr; gap: 0 1.4rem; padding: 1.05rem 0;
    border-top: 1px solid var(--line); }
  .rec-label { display: flex; flex-direction: column; gap: .12rem; }
  .tid { font-family: var(--mono); font-size: .78rem; font-weight: 600; color: var(--accent); }
  .tname { font-size: .68rem; color: var(--muted); line-height: 1.3; }
  .rec-body { min-width: 0; }
  .q { font-family: var(--serif); font-style: italic; font-size: .93rem; color: var(--muted);
    margin: 0 0 .55rem; max-width: 62ch; text-wrap: pretty; }
  .a { font-family: var(--serif); font-size: 1rem; margin: 0; max-width: 66ch;
    text-wrap: pretty; }
  .flag { font-size: .78rem; color: var(--s1); margin: .55rem 0 0; max-width: 66ch;
    padding: .4rem .6rem; border-left: 2px solid var(--s1); background: var(--ground); }
  .rejected { font-size: .88rem; color: var(--muted); margin: 0; max-width: 66ch;
    padding: .55rem .75rem; border-left: 2px solid var(--s2); background: var(--surface);
    border-radius: 0 2px 2px 0; }
  .rejected b { color: var(--s2); }
  .why { margin-top: .6rem; }
  .why summary { font-size: .68rem; text-transform: uppercase; letter-spacing: .09em;
    color: var(--muted); cursor: pointer; width: fit-content; padding: .1rem 0; }
  .why summary:hover { color: var(--accent); }
  .why summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
  .why p { font-size: .82rem; color: var(--muted); margin: .5rem 0 0; padding-left: .9rem;
    border-left: 2px solid var(--line-strong); max-width: 72ch; }

  footer { margin-top: 3.5rem; padding-top: 1.1rem; border-top: 1px solid var(--line);
    font-family: var(--mono); font-size: .72rem; color: var(--muted); }
  footer p { margin: 0 0 .4rem; max-width: 74ch; line-height: 1.6; }

  @media (max-width: 34rem) {
    .rec { grid-template-columns: 1fr; gap: .45rem; }
    .rec-label { flex-direction: row; align-items: baseline; gap: .5rem; }
  }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""


def esc(s):
    return html.escape(str(s), quote=True)


def load_records(target: Path):
    """Records from a combined qa_records.jsonl, a directory holding one, or per-task subdirs."""
    if target.is_file():
        paths = [target]
    else:
        combined = target / "qa_records.jsonl"
        paths = [combined] if combined.exists() else sorted(target.rglob("qa_records.jsonl"))
    if not paths:
        raise SystemExit(f"no qa_records.jsonl found under {target}")
    out = []
    for p in paths:
        out += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return out


def score_cell(value):
    return (
        f'<span class="score s{value}" title="{value} of 5">'
        f'<span class="score-bar"></span><span class="score-num">{value}</span></span>'
    )


def profile_row(subs):
    cells = "".join(
        f'<div class="dom"><span class="dom-name">{esc(label)}</span>{score_cell(subs[key])}</div>'
        for key, label in DOMAINS
        if key in subs
    )
    return f'<div class="profile">{cells}</div>'


def build(records, source_label):
    # Issues come from the checker, not from a hand-written list, so this page
    # cannot claim the batch is cleaner than the checker finds it.
    issues = {}
    for r in records:
        found = check_record(r)
        if found:
            issues[(r["template_id"], r["trial_id"])] = found

    total = len(records)
    valid = sum(1 for r in records if r["validation_status"] == "valid")
    rejected = total - valid
    retried = sum(1 for r in records if (r["model"].get("attempts") or 1) > 1)
    templates = sorted({r["template_id"] for r in records})
    tasks = {}
    for r in records:
        tasks.setdefault(r["procedure_or_task"], []).append(r)

    # ---- summary matrix ----
    rows = []
    for task in sorted(tasks, key=lambda t: (TASK_ORDER.index(t) if t in TASK_ORDER else 99, t)):
        trials = {}
        for r in tasks[task]:
            trials.setdefault(r["trial_id"], []).append(r)
        for trial_id, group in trials.items():
            sa = group[0]["source_annotation"]
            subs = sa["grs_subscores"]
            weakest = min(subs.values())
            weak = [lbl for key, lbl in DOMAINS if subs.get(key) == weakest]
            n_flagged = sum(1 for r in group if (r["template_id"], trial_id) in issues)
            rows.append(
                "<tr>"
                f'<td>{esc(task)}</td>'
                f'<td class="c-trial">{esc(trial_id)}</td>'
                f'<td>{esc(SKILL.get(sa["skill_level"], sa["skill_level"]))}</td>'
                f'<td class="c-num">{esc(sa["grs_total"])}<span class="of">/30</span></td>'
                f'<td class="c-num">{esc(sa.get("video_timestamp_span", ""))}</td>'
                f'<td class="c-weak">{esc(", ".join(weak))}</td>'
                f'<td class="c-num">{len(group)}</td>'
                f'<td class="c-num">{n_flagged or ""}</td>'
                "</tr>"
            )

    # ---- record sections ----
    sections = []
    for task in sorted(tasks, key=lambda t: (TASK_ORDER.index(t) if t in TASK_ORDER else 99, t)):
        trials = {}
        for r in tasks[task]:
            trials.setdefault(r["trial_id"], []).append(r)
        blocks = []
        for trial_id, group in trials.items():
            group.sort(key=lambda r: r["template_id"])
            sa = group[0]["source_annotation"]
            entries = []
            for r in group:
                tid = r["template_id"]
                label = (
                    f'<div class="rec-label"><span class="tid">{esc(tid)}</span>'
                    f'<span class="tname">{esc(TEMPLATE_NAMES.get(tid, ""))}</span></div>'
                )
                if r["validation_status"] != "valid" or not r.get("qa"):
                    entries.append(
                        f'<article class="rec">{label}<div class="rec-body">'
                        '<p class="rejected"><b>No record produced.</b> The model never '
                        "emitted valid JSON for this template, on any attempt. Kept here "
                        "as rejected rather than dropped, so the trial cannot read as one "
                        "more usable record than it is.</p></div></article>"
                    )
                    continue
                qa = r["qa"][0] if isinstance(r["qa"], list) else r["qa"]
                flagged = issues.get((tid, trial_id))
                flag_html = (
                    f'<p class="flag"><b>Checker:</b> {esc("; ".join(flagged))}</p>'
                    if flagged
                    else ""
                )
                entries.append(
                    f'<article class="rec">{label}<div class="rec-body">'
                    f'<p class="q">{esc(qa.get("question", ""))}</p>'
                    f'<p class="a">{esc(qa.get("answer", ""))}</p>'
                    f"{flag_html}"
                    f'<details class="why"><summary>Grounding</summary>'
                    f'<p>{esc(qa.get("rationale", ""))}</p></details>'
                    "</div></article>"
                )
            blocks.append(
                f'<section class="trial"><header class="trial-head"><h3>{esc(trial_id)}</h3>'
                f'<span class="meta">{esc(SKILL.get(sa["skill_level"], sa["skill_level"]))}'
                f' &middot; GRS {esc(sa["grs_total"])}/30'
                f' &middot; {esc(sa.get("video_timestamp_span", ""))}'
                f' &middot; {esc(sa.get("gesture_count", "?"))} gestures</span></header>'
                f'{profile_row(sa["grs_subscores"])}{"".join(entries)}</section>'
            )
        sections.append(f'<section class="task"><h2>{esc(task)}</h2>{"".join(blocks)}</section>')

    # ---- issue block, written from what the checker actually found ----
    if issues:
        items = "".join(
            f"<li><code>{esc(t)} {esc(tr)}</code> &mdash; {esc('; '.join(v))}</li>"
            for (t, tr), v in sorted(issues.items())
        )
        known = (
            '<div class="known"><p><b>'
            f"{len(issues)} record{'s' if len(issues) != 1 else ''} flagged by "
            "<code>check_template_d.py</code>, listed here rather than quietly removed.</b> "
            "Re-sampling a record because the checker dislikes its content selects for output "
            "that passes the checker, which would make the reported quality better than the "
            "pipeline's actual quality.</p>"
            f"<ul>{items}</ul>"
            "<p>Each flagged record is also marked where it appears below.</p></div>"
        )
    else:
        known = (
            '<div class="known clean"><p><b>No record in this batch is flagged by '
            "<code>check_template_d.py</code>.</b> That means no <em>known</em> failure mode "
            "fired -- the checker is pattern matching over problems seen so far, and it cannot "
            "judge whether the coaching is clinically sound.</p></div>"
        )

    kinds = "Type " + " and ".join(sorted({t[0] for t in templates}))
    body = f"""<title>JIGSAWS {esc(kinds)} &middot; {total} records</title>
<style>{CSS}</style>
<div class="wrap">
  <header class="top">
    <h1>{esc(kinds)} records &mdash; {esc(len(tasks))} JIGSAWS task{"s" if len(tasks) != 1 else ""}</h1>
    <div class="runline">
      <span><b>{valid}/{total}</b> valid</span>
      <span>{esc(", ".join(templates))}</span>
      <span>{esc(records[0]["model"].get("model_id", "?"))}</span>
      <span>{rejected} rejected &middot; {retried} needed a retry</span>
      <span><b>{len(issues)}</b> flagged</span>
    </div>
  </header>

  <p class="lede">Every answer below was written by the model from
  <em>annotation text alone</em> &mdash; Global Rating Scale subscores and a
  self-reported experience level per trial. No frame, clip, or video was passed to
  it at any point, so nothing here is an observation; it is coaching reasoned from
  scores, the way a teaching assistant writes feedback from a gradebook without
  having watched the exam. Score profiles appear once per trial because they
  describe the trial, not the individual record.</p>

  {known}

  <div class="matrix-wrap"><table>
    <thead><tr><th>Task</th><th>Trial</th><th>Level</th><th>GRS</th><th>Span</th>
    <th>Weakest domain(s)</th><th>Records</th><th>Flagged</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>

  {"".join(sections)}

  <footer>
    <p>Generated from <code>{esc(source_label)}</code> by
    <code>scripts/build_batch_report.py</code>. Question wording is pinned in
    <code>template-questions-A-D.md</code> and validated by exact match. Coaching
    vocabulary is tied to the OSATS anchors that JIGSAWS' modified GRS derives from:
    an element's own anchor wording is grounded when that element scores low and
    ruled out when it scores high, and a mechanic no anchor mentions &mdash; wrist
    angle, tip control, tremor &mdash; is barred outright.</p>
    <p>Read the flagged count as <em>no known failure mode fired</em>, never as
    <em>the output is correct</em>. An earlier version of this page reported a batch
    as 75/75 with nothing further said, and that batch is now known to contain three
    violations the checker could not yet detect. Every number here is computed from
    the records rather than written into the template, because that earlier version
    hardcoded &ldquo;0 rejected&rdquo; and kept printing it after it stopped being
    true.</p>
    <p>This ran on a 4-bit quantisation locally rather than at full precision on the
    cluster, which is also what caps the context window and causes any truncation
    above. Nothing automated verifies that the coaching is clinically sound &mdash;
    only that it is grounded in the labels and free of the violations the checker
    knows about. Expert review is still required.</p>
  </footer>
</div>
"""
    return body, {"total": total, "valid": valid, "rejected": rejected, "flagged": len(issues)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("batch", help="batch directory or qa_records.jsonl")
    ap.add_argument("-o", "--output", default=None, help="output HTML path")
    args = ap.parse_args()

    target = Path(args.batch)
    records = load_records(target)
    doc, stats = build(records, target.name)
    out = Path(args.output) if args.output else target.with_suffix("") / "report.html"
    if out.is_dir():
        out = out / "report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(
        f"wrote {out} ({len(doc)} chars) | {stats['valid']}/{stats['total']} valid, "
        f"{stats['rejected']} rejected, {stats['flagged']} flagged"
    )


if __name__ == "__main__":
    main()
