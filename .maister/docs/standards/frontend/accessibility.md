## Accessibility

### Semantic HTML
Use appropriate elements (nav, main, button) that convey meaning to assistive technologies.

### Keyboard Navigation
Make all interactive elements accessible via keyboard with visible focus indicators.

### Color Contrast
Maintain 4.5:1 contrast for normal text; don't rely solely on color to convey information.

### Alt Text and Labels
Provide descriptive alt text for images and labels for form inputs.

### Screen Reader Testing
Verify all views work with screen readers.

### ARIA When Needed
Use ARIA attributes to enhance complex components when semantic HTML isn't enough.

### Heading Structure
Use heading levels (h1-h6) in proper order for clear document outline.

### Focus Management
Keep focus order predictable across full-page form submissions and redirects.

## Project-specific standards (discovered)

### Accessible, JavaScript-free pages
Pages work as plain HTML forms without JavaScript. Every input has a `<label for>`; errors use `role="alert"`; navigation uses `aria-label` and `aria-current`; tables have `<caption>` and `th scope`. Layout is mobile-first with a visible `:focus-visible` outline and text contrast of at least 4.5:1. Charts are server-generated inline SVG with axis/unit labels, a visually hidden description and a table fallback.
