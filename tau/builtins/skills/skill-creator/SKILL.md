---
name: skill-creator
description: Create or update Tau skills with effective triggering metadata, focused instructions, and optional reusable scripts, references, or assets. Use when a user asks to create, scaffold, revise, validate, or improve a skill.
---

# Skill Creator

Create concise, reusable skills that give an agent procedural knowledge for a
specific workflow or domain.

This skill is adapted from OpenAI's `skill-creator` skill:
https://github.com/openai/skills/tree/main/skills/.system/skill-creator

## Skill structure

Every skill requires a `SKILL.md` file:

```text
skill-name/
├── SKILL.md
├── scripts/       # optional executable helpers
├── references/    # optional material loaded on demand
└── assets/        # optional templates or output resources
```

Do not add auxiliary files such as `README.md`, `CHANGELOG.md`, or installation
guides. Keep only resources that help an agent perform the skill.

Tau discovers skills in these locations, from highest to lowest priority:

1. `<project>/.tau/skills/`
2. `~/.tau/skills/`
3. Tau's built-in `tau/builtins/skills/`

If the user does not specify a destination, ask whether the skill should be
project-local or global. Use project-local when the instructions depend on one
repository; use global when they apply across projects.

## Design principles

### Keep context small

Assume the agent already understands general software and reasoning concepts.
Include only non-obvious domain knowledge, constraints, or procedures. Prefer a
short example over a long explanation.

Keep `SKILL.md` below 500 lines. Move detailed material into `references/` and
state exactly when to read each reference.

### Match specificity to risk

- Use flexible prose when several approaches are valid.
- Use pseudocode or parameterized helpers when a preferred pattern exists.
- Use deterministic scripts and explicit steps when the workflow is fragile.

### Use progressive disclosure

Skill content has three levels:

1. Frontmatter name and description, always shown to the agent.
2. The `SKILL.md` body, loaded when the skill is selected.
3. Bundled resources, loaded or executed only when required.

Keep references one level below `SKILL.md`. Avoid chains where one reference
points to another reference needed to complete the workflow.

## Creation workflow

### 1. Establish concrete usage

Identify examples of requests that should trigger the skill and the expected
outcome for each. Ask one focused question at a time only when the intended
behavior cannot be inferred safely.

Conclude this step when the skill's scope and triggering requests are clear.

### 2. Plan reusable content

For each example:

1. Determine how an agent would complete it from scratch.
2. Identify repeated work or knowledge.
3. Decide whether that content belongs in `SKILL.md`, `scripts/`,
   `references/`, or `assets/`.

Create only the directories the skill actually uses.

### 3. Create the skill

Normalize the name to lowercase hyphen-case using letters, digits, and hyphens,
with a maximum length of 64 characters. Name the folder exactly after the skill.

Create `SKILL.md` with this minimal structure:

```markdown
---
name: example-skill
description: State what the skill does and the specific requests or contexts that should trigger it.
---

# Example Skill

State the workflow as direct instructions.
```

The description is the primary trigger. Put all "when to use" information in
the description because Tau sees it before loading the body.

Tau supports `disable-model-invocation: true` as an optional frontmatter field
for skills that should be available only through explicit `/skill:<name>`
invocation.

### 4. Write the instructions

Use imperative language. Organize the body around the workflow or tasks the
agent must perform. Include decision criteria, validation steps, and failure
handling when they are not obvious.

For resources:

- Put repeatable deterministic operations in `scripts/` and test them by
  running them.
- Put detailed schemas, policies, and variant-specific guidance in
  `references/`.
- Put templates and files intended for generated output in `assets/`.
- Link every required resource directly from `SKILL.md` and explain when it is
  needed.

Do not duplicate the same information in the body and a reference.

### 5. Validate

Check that:

- `SKILL.md` exists and has a non-empty body.
- Frontmatter contains a valid `name` and a descriptive `description`.
- The directory name matches the skill name.
- Every linked resource exists.
- No placeholder text or unused example resource remains.
- Added scripts execute successfully.
- Tau loads the skill without diagnostics.

Use Tau's loader for a direct validation when working in the Tau repository:

```bash
python -c "from pathlib import Path; from tau.skills.loader import load_skills_from_dir; print(load_skills_from_dir(Path('path/to/skills')))"
```

### 6. Iterate

Use the skill on realistic requests. If the agent misses steps, loads irrelevant
material, or triggers incorrectly, revise the description, workflow, or
resource boundaries and validate again.
