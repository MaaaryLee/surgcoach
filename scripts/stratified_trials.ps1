# The 15 trials used by the Template C and D batches, one list so the two cannot
# drift apart and every video ends up with both a C and a D record set.
#
# Chosen to span each task's GRS range rather than taken alphabetically. The
# earlier selection was B001-B004 plus C001 per task, which is simply the front of
# the file listing: 13 of those 15 scored 17 or below out of 30, all five Suturing
# trials were novice, and no expert trial appeared anywhere. Two templates cannot
# do their job on a sample like that -- C7 has five supervision levels and used one
# for all 15 records, and D5 is asked to name a strongest skill where none of the
# trials has a strength to name.
#
# The spread also decouples skill_level from grs_total, which the old selection
# could not: Suturing_D004 is an expert scoring 8/30 and Knot_Tying_H004 a novice
# scoring 22/30. Those cases test whether the model reasons from the scores or just
# parrots the self-reported experience label.
#
#   Suturing        D004 E  8 | H001 N 14 | E004 E 19 | I004 N 23 | C004 I 30
#   Knot_Tying      G004 N  6 | F004 I 10 | F005 I 15 | E002 E 19 | H004 N 22
#   Needle_Passing  C003 I  7 | D002 E 11 | E003 E 13 | B004 N 19 | F004 I 24

$StratifiedTrials = @(
    @{ Task = "Suturing";       Trials = "Suturing_D004,Suturing_H001,Suturing_E004,Suturing_I004,Suturing_C004" },
    @{ Task = "Knot_Tying";     Trials = "Knot_Tying_G004,Knot_Tying_F004,Knot_Tying_F005,Knot_Tying_E002,Knot_Tying_H004" },
    @{ Task = "Needle_Passing"; Trials = "Needle_Passing_C003,Needle_Passing_D002,Needle_Passing_E003,Needle_Passing_B004,Needle_Passing_F004" }
)
