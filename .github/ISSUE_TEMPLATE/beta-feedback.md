---
name: Beta feedback
about: Report scanner ergonomics, coverage gaps, or workflow fit during the 0.1.0bN beta.
title: '[beta] '
labels: ['beta']
---

<!-- Thanks for testing OpenACA. Fields marked (required) are the ones I look at first. -->

### Feedback type (required)

<!-- Pick the closest fit — helps me triage. -->

- [ ] Scanner ergonomics — install / first scan / output legibility / CLI friction
- [ ] Coverage gap — something the scanner inventoried or missed that surprised me
- [ ] Workflow fit — where OpenACA does or doesn't fit in my security tooling

### Command run (required)

```bash
# Paste the full command you ran, e.g.:
# openaca scan repo --target ./my-mcp-server --include-posture
```

### OpenACA version (required)

```
# Output of `openaca --version`
```

### Expected vs actual (required)

**Expected:**

**Actual:**

### Output (redacted as needed)

<!--
Paste scanner output. If it contains internal names, paths, or component IDs
you don't want public, redact freely — replace with <redacted> or generic
placeholders. The shape of the output is more useful than the literal
contents. SARIF is sometimes easier to redact than text.
-->

```
```

### Missing or incorrect inventory

<!--
If the scanner missed something it should have inventoried, or inventoried
something incorrectly, describe what + where. Format hint:
- Manifest: path/to/file
- Should have shown: <name@version>
- Actually showed: <empty | wrong name | wrong version>
Leave blank if this isn't an inventory issue.
-->

### Environment

- OS:
- Python version (`python --version`):
- Install path (`pip install ...` / `uv sync` / other):

### Anything else?

<!-- Workflow context, what you wish it did, why you tried it, etc. -->
