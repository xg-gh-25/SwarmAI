# Weasel Words Guide

Weasel words are terms or statements that are intentionally ambiguous, vague, or misleading. They often create an impression that something specific and meaningful has been said, when in fact only a vague or ambiguous claim has been communicated. This skill helps identify and avoid weasel words in documentation, plans, and code.

## What Are Weasel Words?

Weasel words derive their name from the way weasels can supposedly suck the contents out of an egg while leaving the shell intact, creating an appearance of substance where none exists. In communication, weasel words:

- Create ambiguity
- Avoid commitment
- Lack specificity
- Inflate importance without substance
- Allow plausible deniability
- Obscure meaning or responsibility

## Words to Avoid Entirely

The following words add no value to internal documents and should be avoided:

### Abstract Qualifiers
- About
- Almost
- Around
- Close(ly)
- Considerably
- Essentially
- Generally
- Just
- Main(ly)
- Only
- Primarily
- Somewhat
- Typical(ly)
- Usually
- Very
- Various

### Uncertain Verbs
- Aim to
- Believe
- Can (as in could)
- Could
- Expect
- Feel
- Hope
- May
- Maybe
- Might
- Planning
- Seems
- Should
- Strive
- Think
- Try
- Would

### Vague Descriptors
- Aligned
- Collaborated
- Common
- Dramatic(ally)
- Easy
- Effective(ly)
- Eh
- Enable(s)
- Established
- Extreme(ly)
- Flexible/-y
- Great
- In Due Time
- Lot/Lots
- Material
- Meticulous(ly)
- Non-Trivial
- Overwhelming
- Partner With
- Poor
- Possibly
- Seamless(ly)
- Soon
- Streamline
- Supports
- Synergy
- Trending
- Trivial
- Was

## Words to Use with Caution (Only if Quantified)

These words should only be used when accompanied by specific metrics, numbers, or concrete examples:

### Comparative Terms
- Always
- Better
- Bigger
- Disproportionate(ly)
- Fast(er)
- Great(er)
- High(er)
- Large(r)
- Low(er)
- More
- Most
- Relative(ly)
- Risky/-ier
- Safe(r)
- Slow(er)
- Small(er)
- Strong(ly)
- Worse

### Quantity Indicators
- Could
- Efficient/-cy
- Few
- Frequent(ly)
- Future
- Many
- Multiple
- Near(ly)
- Often
- Optimize
- Several
- Significant(ly)
- Some
- Various

## Weasel Words in Plans

Be especially cautious about using these terms in project plans:

| Unclear Term | Better Approach |
|-------------|----------------|
| Additional scope | Specify the exact implication. Is a change to the plan needed or not? |
| Behind | Clarify the impact. Is a change to the plan needed or not? |
| Beta/UAT | Define the phase clearly with specific exit criteria and what follows. |
| Complexity | Define it precisely and explain what it means for the plan. |
| Green status | Quantify with "X of Y milestones achieved" or "ETA earlier than target date by Z days." |
| In Gamma/Shadow/Prod | Clarify whether this meets a specific milestone or launch criteria. |
| Red status | Explain why the date is no longer possible and what specific action should be taken. |
| Yellow status | Detail why the milestone is at risk and what specific actions will bring it to Green. If no actions can help, the status is actually Red. |

## Weasel Words in Code

Weasel words in code bloat your codebase without adding value. They often indicate loose cohesion or violations of the Single Responsibility Principle.

### Examples to Avoid

- `BeerUtility` - "Utility" is redundant. All classes should be useful, or they should be deleted.
- `BeerHelper` - Too vague. What does this class help with specifically?
- `Constants` - Organizing identifiers by language keyword is not useful. Be more specific about the domain these constants relate to.

### Better Alternatives

- Instead of `UserUtility`, use `UserAuthentication` or `UserValidator`
- Instead of `PaymentHelper`, use `PaymentProcessor` or `PaymentCalculator`
- Instead of `Constants`, use `ConfigurationValues`, `ApiEndpoints`, or `DatabaseColumns`

## How to Identify and Replace Weasel Words

1. **Ask for specifics**: When you see a weasel word, ask "how much?", "when exactly?", or "in what way?"
2. **Quantify**: Replace qualitative terms with quantitative measurements
3. **Be concrete**: Use specific examples instead of general statements
4. **Use active voice**: Clarify who is doing what
5. **Set deadlines**: Replace "soon" with actual dates
6. **Define terms**: If you must use a technical term, define it clearly

## Examples of Replacements

| Weasel Words | Better Alternative |
|-------------|-------------------|
| "We will launch soon" | "We will launch on October 15, 2025" |
| "Performance significantly improved" | "Response time decreased from 300ms to 120ms" |
| "Many users reported issues" | "42 users reported login failures between 2-4pm" |
| "The system is more efficient" | "The system uses 30% less memory and completes tasks in half the time" |
| "We should improve this" | "We will refactor the authentication module by June 1" |

## Resources

- [Wikipedia - Weasel Word](https://en.wikipedia.org/wiki/Weasel_word)
- [Key words for use in RFCs to Indicate Requirement Levels](https://datatracker.ietf.org/doc/html/rfc2119)
