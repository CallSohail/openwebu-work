# Releasing Extensions

Each extension in this repository is versioned independently.

## Release checklist

Before publishing a version:

1. Update the plugin metadata version in the Python file.
2. Update the plugin README if behavior, configuration, or compatibility changed.
3. Add an entry to the plugin changelog when one exists.
4. Confirm no secrets, private endpoints, personal data, or deployment-specific identifiers were added.
5. Test the extension on the minimum documented Open WebUI version and, when practical, the current release.
6. Test at least one model with native tool calling and one plain text model when the plugin claims model-independent behavior.
7. Merge the release changes into `main`.
8. Create a Git tag using the plugin-specific format below.
9. Optionally create a GitHub Release from that tag with the changelog entry as release notes.

## Tag naming

Use:

```text
<plugin-name>-v<version>
```

Current recommended tags:

```text
study-mode-v1.0.0
ragflow-v3.0.0
```

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

### Compatibility
- Open WebUI: ...

### Upgrade notes
- ...
```
