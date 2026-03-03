# Design Document: {Skill Name}

## Overview

This skill {brief description of what it does}. It guides users through {workflow summary} to produce {output type}.

**Why?** {One sentence explaining the problem this solves—what pain point or gap motivated this skill's existence.}

## Architecture

The skill follows a {simple linear | multi-step workflow | complex conditional} pattern {with/without} external dependencies or scripts.

```
┌─────────────────────────────────────────────────────────────────┐
│                        {Skill Name}                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   {Phase 1}  │───▶│   {Phase 2}  │───▶│   {Phase 3}  │       │
│  │   {Action}   │    │   {Action}   │    │   {Action}   │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│         │                   │                   │                │
│         ▼                   ▼                   ▼                │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │ - {Input 1}  │    │ - {Output 1} │    │ - {Check 1}  │       │
│  │ - {Input 2}  │    │ - {Output 2} │    │ - {Check 2}  │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                  REFERENCE.md (if needed)                 │   │
│  │  - {Reference content description}                        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Components and Interfaces

### Component 1: {Input Gatherer / Context Collector}

Collects necessary information from the user before {processing/generating}.

**Inputs:**
- {Input 1}: {description}
- {Input 2}: {description}
- {Input 3}: {description} (optional)

**Outputs:**
- Validated context ready for {next phase}

**Validation Rules:**
- {Rule 1, e.g., "Minimum 2 examples required"}
- {Rule 2, e.g., "No overlap between X and Y"}
- {Rule 3}

### Component 2: {Generator / Processor}

Produces the {output type} based on gathered context.

**{Output Section 1}:**
- {What it includes}
- {Quality constraints}
- Target: {e.g., "Under 100 words"}

**{Output Section 2}:**
- {What it includes}
- {Quality constraints}
- Target: {e.g., "1-3 items"}

### Component 3: {Quality Validator}

Ensures generated output meets quality standards.

**Checks:**
- {Check 1, e.g., "Word count per section"}
- {Check 2, e.g., "No vague terms without specifics"}
- {Check 3, e.g., "Consistent formatting"}
- {Check 4, e.g., "No anti-patterns"}

### Component 4: {Reference Provider} (if applicable)

Provides quick access to {domain knowledge/reference information}.

**Capabilities:**
- Display {reference content}
- Help identify {appropriate choices}
- Surface {relevant guidance}

## Data Models

### Input Model

```
{InputContext}:
  {field_1}: {type}          # {description}
  {field_2}: {type}          # {description}
  {field_3}: {type}[]        # {description}
  {field_4}: {type}          # (optional) {description}
```

### {Domain Object} Model (if applicable)

```
{DomainObject}:
  {field_1}: {type}          # {description}
  {field_2}: {type}          # {description}
  {field_3}: {type}[]        # {description}
```

### Output Model

```
{OutputModel}:
  {section_1}:
    {field}: {type}          # {description}
    {field}: {type}[]        # {description}
  {section_2}:
    {field}: {type}          # {description}
```

### Quality Check Result Model

```
QualityCheckResult:
  passed: boolean
  issues: QualityIssue[]

QualityIssue:
  type: "{issue_type_1}" | "{issue_type_2}" | "{issue_type_3}"
  description: string
  suggestion: string
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

{For workflow skills without executable code, note that properties define quality standards validated through output inspection rather than automated testing.}

### Property 1: {Constraint Name}
*For any* generated output, {the constraint that must hold}.
**Validates: Requirements {X.Y, X.Z}**

### Property 2: {Quality Standard Name}
*For any* generated output, the text SHALL {positive requirement} and SHALL NOT {negative requirement}.
**Validates: Requirements {X.Y}**

### Property 3: {Input Inclusion}
*For any* generated output where the user provided {input type}, the output SHALL {reference/incorporate} elements from that input.
**Validates: Requirements {X.Y}**

### Property 4: {Specificity Requirement}
*For any* generated output, the text SHALL NOT contain {vague/problematic patterns} without {required context}.
**Validates: Requirements {X.Y}**

### Property 5: {Reference Accuracy}
*For any* generated output, the text SHALL explicitly reference the {selections/choices} made by the user.
**Validates: Requirements {X.Y}**

### Property 6: {Format Compliance}
*For any* generated output, the output SHALL be in {required format} and SHALL NOT be formatted as {prohibited format}.
**Validates: Requirements {X.Y}**

### Property 7: {Tone/Framing}
*For any* generated {section type}, the text SHALL use {positive framing} rather than {negative framing}.
**Validates: Requirements {X.Y}**

### Property 8: {Validation Rule}
*For any* valid input, {constraint that must hold, e.g., "set A and set B SHALL have no intersection"}.
**Validates: Requirements {X.Y}**

### Property 9: {Structure Compliance}
*For any* generated output:
- {Section 1} SHALL {constraint}
- {Section 2} SHALL {constraint}
**Validates: Requirements {X.Y}**

### Property 10: {No Placeholder Text}
*For any* generated output, the text SHALL NOT contain placeholder phrases ({list of prohibited placeholders}).
**Validates: Requirements {X.Y}**

## Error Handling

| Error Condition | Handling Strategy |
|-----------------|-------------------|
| {Error 1, e.g., "Insufficient input"} | {How to handle, e.g., "Prompt for additional input with guidance"} |
| {Error 2, e.g., "Invalid selection"} | {How to handle} |
| {Error 3, e.g., "Output exceeds limit"} | {How to handle} |
| {Error 4, e.g., "Conflicting inputs"} | {How to handle} |
| {Error 5, e.g., "Missing required field"} | {How to handle} |

## Testing Strategy

### Approach

{Describe the testing approach based on skill type:}
- For workflow skills: Manual validation of outputs against quality properties
- For skills with scripts: Unit tests + integration tests
- Example-based testing with diverse input scenarios
- Checklist verification for each generated output

### Test Scenarios

| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Happy path | {Complete valid input} | {Generate compliant output} |
| Minimum input | {Bare minimum input} | {Handle gracefully or request more} |
| Invalid input | {Input that violates rules} | {Reject and ask for correction} |
| Edge case | {Unusual but valid input} | {Handle correctly} |
| Reference request | {User asks for help} | {Provide appropriate guidance} |

### Quality Checklist

Before finalizing any generated output, verify:

- [ ] {Check 1, e.g., "Word count within limits"}
- [ ] {Check 2, e.g., "No prohibited patterns"}
- [ ] {Check 3, e.g., "Required elements present"}
- [ ] {Check 4, e.g., "Format compliance"}
- [ ] {Check 5, e.g., "Tone appropriate"}
- [ ] {Check 6, e.g., "Input referenced"}
- [ ] {Check 7, e.g., "No placeholders"}

### Validation Commands (if applicable)

{For skills with scripts:}
```bash
# {Description of what this validates}
python scripts/{script_name}.py --help

# {Description of what this validates}
python scripts/{script_name}.py --input test_data.json
```

{For workflow skills:}
Validation is performed by:
1. Reading generated output
2. Checking against quality checklist
3. Comparing to example patterns from EXAMPLES.md

---

## Template Usage Notes

> [!TIP]
> **Customize this template:**
> 1. Replace all `{placeholders}` with skill-specific content
> 2. Add/remove components based on skill complexity
> 3. Add/remove correctness properties based on requirements
> 4. Ensure each property references specific requirements

> [!IMPORTANT]
> **Complexity Guidelines:**
> - Simple skills: 3-5 properties, 1-2 components
> - Standard skills: 5-10 properties, 2-4 components
> - Complex skills: 10+ properties, 4+ components, scripts
