# Releasing Extensions

Each extension in this repository is versioned independently.

## Release checklist

Before publishing a version:

1. Update the plugin metadata version in the Python file.
2. Update the plugin README if behavior, configuration, or compatibility changed.
3. Add an entry to the plugin changelog. Every extension keeps one beside its implementation; see the section names in the template below.
4. Confirm no secrets, private endpoints, personal data, or deployment-specific identifiers were added.
5. Test the extension on the minimum documented Open WebUI version and, when practical, the current release.
6. Test at least one model with native tool calling and one plain text model when the plugin claims model-independent behavior.
7. Merge the release changes into `main`.
8. Create an annotated Git tag using the plugin-specific format below, carrying the changelog entry as its message:

   ```bash
   git tag -a <plugin-name>-v<version> <commit> -F notes.md
   git push origin <plugin-name>-v<version>
   ```

9. Publish a GitHub Release from that tag with the same notes, and add it to the table below.

## Tag naming

Use:

```text
<plugin-name>-v<version>
```

## Published releases

| Extension | Version | Tag | Released |
| --- | --- | --- | --- |
| [Secure Dynamic Onboarding](functions/events/secure-onboarding/) | 9.1.0 | `secure-onboarding-v9.1.0` | 2026-09-18 |
| [Rich Email Composer](tools/email-composer/) | 1.0.0 | `email-composer-v1.0.0` | 2026-09-11 |
| [Study Mode](functions/filters/study-mode/) | 1.1.0 | `study-mode-v1.1.0` | 2026-08-28 |
| [Quick Actions](functions/actions/quick-actions/) | 3.0.1 | `quick-actions-v3.0.1` | 2026-08-28 |
| [RAGFlow Advanced Connector](tools/ragflow/) | 3.0.0 | `ragflow-v3.0.0` | 2026-08-26 |

Superseded tags stay in place: `study-mode-v1.0.0` and `quick-actions-v3.0.0`.

From `study-mode-v1.1.0` onward, a tag points at the commit where that version's Python file last changed, so the tag resolves to the code that was released rather than to a later documentation commit. The two tags above predate this rule: `quick-actions-v3.0.0` happens to follow it, `study-mode-v1.0.0` points at that release's final documentation commit.

This avoids collisions between independently versioned extensions in the same repository.

## Suggested repository topics

These GitHub repository topics make the project easier to discover:

```text
open-webui
openwebui
open-webui-tools
open-webui-functions
llm
ai-agents
python
rag
ragflow
study-mode
education
socratic-learning
```

Keep repository topics broad. Use plugin-specific tags in each plugin README for finer classification.

## Release notes template

```markdown
## <Plugin Name> <version>

### Added
- ...

### Changed
- ...

### Fixed
- ...

### Security
- ...

### Compatibility
- Open WebUI: ...

### Upgrade notes
- ...
```

The plugin changelog uses the same sections under a `## [<version>] - YYYY-MM-DD` heading. Omit any section with nothing in it.
