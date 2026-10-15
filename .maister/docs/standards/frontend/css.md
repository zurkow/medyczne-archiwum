## CSS

### Consistent Methodology
Stick to the project's plain-CSS approach with kebab-case semantic class names across the entire codebase; no CSS framework.

### Design Tokens
Establish and document consistent values for colors, spacing, and typography.

### Minimize Custom CSS
Reuse existing classes before adding new ones; keep the stylesheet small.

## Project-specific standards (discovered)

### Single local stylesheet
Use the single local `/static/app.css` — no CDNs, web fonts or external URLs (system-ui font stack). Class names are kebab-case (`site-header`, `page-content`, `table-scroll`, `visually-hidden`); sizes in `rem`.
