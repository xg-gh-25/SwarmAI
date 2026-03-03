# Requirements Document: {Skill Name}

## Introduction

This document defines the requirements for an Agent skill that {brief description of what the skill does}. The skill helps users {primary value proposition} by {how it achieves this}.

## Glossary

- **Skill_System**: The skill that {primary function}
- **{Domain_Term_1}**: {Definition}
- **{Domain_Term_2}**: {Definition}
- **User**: The person invoking the skill

## Requirements

### Requirement 1: {Primary Capability}

**User Story:** As a user, I want to {action}, so that {benefit}.

#### Acceptance Criteria

1. WHEN the skill activates, THE Skill_System SHALL {initial action}
2. WHEN {trigger event}, THE Skill_System SHALL {response}
3. IF {error condition}, THEN THE Skill_System SHALL {error handling}
4. THE Skill_System SHALL {constraint or quality requirement}

### Requirement 2: {Secondary Capability}

**User Story:** As a user, I want to {action}, so that {benefit}.

#### Acceptance Criteria

1. WHEN {trigger}, THE Skill_System SHALL {response}
2. THE Skill_System SHALL {quality constraint}
3. IF {edge case}, THEN THE Skill_System SHALL {handling}

### Requirement 3: {Input Handling}

**User Story:** As a user, I want to provide {input type}, so that the skill can {process it correctly}.

#### Acceptance Criteria

1. WHEN the user provides {valid input}, THE Skill_System SHALL {process it}
2. IF the user provides {invalid input}, THEN THE Skill_System SHALL {request correction}
3. THE Skill_System SHALL validate {input constraints}

### Requirement 4: {Output Generation}

**User Story:** As a user, I want to receive {output type}, so that I can {use it for purpose}.

#### Acceptance Criteria

1. WHEN generating output, THE Skill_System SHALL {format requirement}
2. THE Skill_System SHALL {quality standard}
3. THE Skill_System SHALL {constraint, e.g., word count, structure}

### Requirement 5: {Reference/Knowledge Access}

**User Story:** As a user, I want access to {domain knowledge}, so that I can {make informed decisions}.

#### Acceptance Criteria

1. WHEN the user requests reference information, THE Skill_System SHALL {provide it}
2. THE Skill_System SHALL {organize/present information}
3. WHEN the user is unsure about {decision}, THE Skill_System SHALL {help them decide}

### Requirement 6: {Quality Standards}

**User Story:** As a user, I want output that meets {quality bar}, so that {outcome}.

#### Acceptance Criteria

1. THE Skill_System SHALL {quality rule 1}
2. THE Skill_System SHALL {quality rule 2}
3. THE Skill_System SHALL avoid {anti-pattern}
4. IF {quality violation detected}, THEN THE Skill_System SHALL {correction action}

### Requirement 7: {Structure/Format}

**User Story:** As a user, I want output in {specific format}, so that it is {consistent/usable}.

#### Acceptance Criteria

1. THE Skill_System SHALL structure output with {sections/format}
2. THE Skill_System SHALL {formatting constraint}
3. THE Skill_System SHALL avoid {format anti-pattern}

### Requirement 8: {Tone/Style} (if applicable)

**User Story:** As a user, I want output with {appropriate tone}, so that it is {well-received/effective}.

#### Acceptance Criteria

1. THE Skill_System SHALL use {positive/professional/appropriate} framing
2. THE Skill_System SHALL avoid {negative patterns}
3. WHEN {sensitive content}, THE Skill_System SHALL {handle appropriately}

---

## Template Usage Notes

> [!TIP]
> **Customize this template:**
> 1. Replace all `{placeholders}` with skill-specific content
> 2. Add/remove requirements based on skill complexity
> 3. Ensure each requirement has testable acceptance criteria
> 4. Use EARS patterns consistently (WHEN/IF/THE/SHALL)

> [!IMPORTANT]
> **EARS Pattern Reference:**
> - **Ubiquitous**: THE system SHALL {response}
> - **Event-driven**: WHEN {trigger}, THE system SHALL {response}
> - **State-driven**: WHILE {condition}, THE system SHALL {response}
> - **Unwanted event**: IF {condition}, THEN THE system SHALL {response}
> - **Optional feature**: WHERE {option}, THE system SHALL {response}
