## Components

### Single Responsibility
Each template or partial should do one thing well.

### Reusability
Design partials to work across different contexts with explicit context variables.

### Composability
Build complex pages by combining smaller partials (`{% include %}`) rather than creating monoliths.

### Clear Interface
Document the context variables a template or partial expects.

### Encapsulation
Keep implementation details private; expose only what's necessary.

### Consistent Naming
Use descriptive names that indicate purpose and follow team conventions.

### Minimal Context
If a template needs many context variables, consider composition or splitting it.

## Project-specific standards (discovered)

### Jinja templates extend base.html
Authenticated pages `{% extends "base.html" %}` and fill `{% block content %}` with `<section class="panel" aria-labelledby="...">` and an `h1` with the matching id. Partials (e.g. `trend_chart.html`) are `{% include %}`d. Pre-auth pages (login, register, invite) are standalone documents with `lang="pl"`. Render templates only through `web.templates.render_template` with a `title`. Server-rendered Jinja2 only — no frontend framework.

### No inaccessible data in HTML
Rendered HTML/SVG contains only already-authorized data — never data of users the viewer cannot access. Show query errors explicitly.
