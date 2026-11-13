# Case index

Source: [heuristic-report.json](results/heuristic-report.json), run 43495705978.

All IDs are exact keys in the raw report. not_repaired is excluded from the confusion matrix; UNREVIEWED is no alert in the operational oracle matrix.

## Repair

| ID | Status | Masking | Warning | Effect | Attempts |
|---|---|---|---|---|---:|
| ceil_div/doc/bad-test | classified | false | true | corrected_bad_test | 1 |
| ceil_div/doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| ceil_div/no-doc/bad-test | classified | false | false | corrected_bad_test | 1 |
| ceil_div/no-doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| eligible/doc/bad-test | not_repaired | N/A | false | not_repaired | 2 |
| eligible/doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| eligible/no-doc/bad-test | not_repaired | N/A | false | not_repaired | 2 |
| eligible/no-doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| magnitude/doc/bad-test | classified | false | true | corrected_bad_test | 1 |
| magnitude/doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| magnitude/no-doc/bad-test | classified | false | false | corrected_bad_test | 1 |
| magnitude/no-doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| occurrences/doc/bad-test | classified | false | true | corrected_bad_test | 1 |
| occurrences/doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| occurrences/no-doc/bad-test | classified | false | false | corrected_bad_test | 1 |
| occurrences/no-doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| reciprocal/doc/bad-test | not_repaired | N/A | false | not_repaired | 2 |
| reciprocal/doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| reciprocal/no-doc/bad-test | not_repaired | N/A | false | not_repaired | 2 |
| reciprocal/no-doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| remove_prefix/doc/bad-test | not_repaired | N/A | false | not_repaired | 2 |
| remove_prefix/doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| remove_prefix/no-doc/bad-test | not_repaired | N/A | false | not_repaired | 2 |
| remove_prefix/no-doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| safe_divide/doc/bad-test | classified | false | true | corrected_bad_test | 1 |
| safe_divide/doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| safe_divide/no-doc/bad-test | classified | false | false | corrected_bad_test | 1 |
| safe_divide/no-doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| sum_values/doc/bad-test | classified | false | true | corrected_bad_test | 1 |
| sum_values/doc/bug | not_repaired | N/A | false | not_repaired | 2 |
| sum_values/no-doc/bad-test | classified | false | false | corrected_bad_test | 1 |
| sum_values/no-doc/bug | classified | true | false | oracle_changed | 1 |

## Oracle

| ID | Bug | Verdict | Warning | Explanation |
|---|---|---|---|---|
| ceil_div/doc/mutant | true | ok | false | The function returns the integer quotient of a divided by b, which is consistent with the docstring and the function name. |
| ceil_div/doc/reference | false | ok | false |  |
| ceil_div/no-doc/mutant | true | unreviewed | false |  |
| ceil_div/no-doc/reference | false | unreviewed | false |  |
| eligible/doc/mutant | true | ok | false | The function returns True if age is at least 18, which matches the docstring. |
| eligible/doc/reference | false | ok | false |  |
| eligible/no-doc/mutant | true | unreviewed | false |  |
| eligible/no-doc/reference | false | unreviewed | false |  |
| magnitude/doc/mutant | true | ok | false |  |
| magnitude/doc/reference | false | ok | false | The function correctly returns the absolute value of x, which is consistent with the documented intent. |
| magnitude/no-doc/mutant | true | unreviewed | false |  |
| magnitude/no-doc/reference | false | unreviewed | false |  |
| occurrences/doc/mutant | true | ok | false | The function correctly counts the occurrences of the value in the list. |
| occurrences/doc/reference | false | ok | false | The function correctly counts the occurrences of the specified value in the list. |
| occurrences/no-doc/mutant | true | unreviewed | false |  |
| occurrences/no-doc/reference | false | unreviewed | false |  |
| reciprocal/doc/mutant | true | ok | false | The function returns 0 when b is 0, which is consistent with the docstring and the function name. |
| reciprocal/doc/reference | false | suspicious | true | The function name 'reciprocal' suggests it should return the reciprocal of a number, but it actually returns the integer division of 1 by the input number, which is not what the function name implies. |
| reciprocal/no-doc/mutant | true | unreviewed | false |  |
| reciprocal/no-doc/reference | false | unreviewed | false |  |
| remove_prefix/doc/mutant | true | ok | false | The function removes exactly one leading prefix, which matches the documented intent. |
| remove_prefix/doc/reference | false | ok | false | The function removes exactly one leading prefix as specified in the docstring. |
| remove_prefix/no-doc/mutant | true | unreviewed | false |  |
| remove_prefix/no-doc/reference | false | unreviewed | false |  |
| safe_divide/doc/mutant | true | suspicious | true | The function returns zero when b is zero, which contradicts the documentation that it should raise ZeroDivisionError when b is zero. |
| safe_divide/doc/reference | false | ok | false | The function returns 0 when b is zero, which is consistent with the documented intent. |
| safe_divide/no-doc/mutant | true | unreviewed | false |  |
| safe_divide/no-doc/reference | false | unreviewed | false |  |
| sum_values/doc/mutant | true | ok | false | The function returns the sum of the values plus one, which is consistent with the documented intent. |
| sum_values/doc/reference | false | ok | false |  |
| sum_values/no-doc/mutant | true | unreviewed | false |  |
| sum_values/no-doc/reference | false | unreviewed | false |  |

The only oracle true positive (safe_divide/doc/mutant) has a reversed, incorrect explanation. A correct label is not a correct diagnosis.
