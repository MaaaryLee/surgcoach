"""Repair UTF-8-read-as-cp1252 damage in already-generated qa_records.jsonl.

Model responses partway through development arrived with UTF-8 punctuation
decoded as cp1252, so an em-dash reads as "â€”" and a right single quote as
"â€™". The runner now repairs this at generation time; this script fixes
batches produced before that.

The transform is exactly invertible, so this recovers the original text rather
than deleting or substituting characters. It is applied only where the signature
is present and only where the round trip is lossless, so clean text and genuinely
accented text are untouched. Rewrites in place and reports what changed.

    python3 scripts/repair_mojibake_in_records.py <dir-or-jsonl> [...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_annotation_qa_jigsaws import MOJIBAKE_SIGNATURE, repair_mojibake


def walk(value):
    """Repair every string in a nested structure, counting the fixes."""
    if isinstance(value, str):
        fixed = repair_mojibake(value)
        return fixed, int(fixed != value)
    if isinstance(value, dict):
        out, n = {}, 0
        for k, v in value.items():
            out[k], c = walk(v)
            n += c
        return out, n
    if isinstance(value, list):
        out, n = [], 0
        for v in value:
            fv, c = walk(v)
            out.append(fv)
            n += c
        return out, n
    return value, 0


def main(targets):
    if not targets:
        print("usage: repair_mojibake_in_records.py <dir-or-jsonl> [...]")
        return 1
    grand_files = grand_fields = 0
    for t in targets:
        p = Path(t)
        paths = sorted(p.rglob("qa_records.jsonl")) if p.is_dir() else [p]
        for path in paths:
            records, fields, affected = [], 0, 0
            for line in path.open(encoding="utf-8"):
                rec = json.loads(line)
                fixed, n = walk(rec)
                records.append(fixed)
                fields += n
                affected += 1 if n else 0
            if fields == 0:
                print(f"  clean, unchanged: {path}")
                continue
            with path.open("w", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"  repaired {fields} field(s) across {affected} record(s): {path}")
            grand_files += 1
            grand_fields += fields
    print(f"\n{grand_files} file(s) rewritten, {grand_fields} field(s) repaired")
    # confirm nothing is left
    left = 0
    for t in targets:
        p = Path(t)
        for path in (sorted(p.rglob("qa_records.jsonl")) if p.is_dir() else [p]):
            if MOJIBAKE_SIGNATURE in path.read_text(encoding="utf-8"):
                print(f"!! signature still present: {path}")
                left += 1
    print("verified: no signature remains" if not left else f"{left} file(s) still affected")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
