# The Sitdown desktop design system

Every screen is built from `sitdown_ui.py`. Screens ask for a **role** (a colour such as `surface` or `danger`, a text style such as `heading`) or a **component**. They never hard-code colours or fonts. The design follows platform conventions (the macOS HIG and Material 3 ideas) without copying either: one accent colour, clear hierarchy, generous spacing, and state never shown by colour alone.

## Tokens

**Colour roles** (`PALETTES['dark']` / `PALETTES['light']`):

| Group | Roles |
| --- | --- |
| Surfaces | `bg`, `sidebar`, `surface`, `surface_alt`, `raised`, `field`, `border` |
| Text | `text`, `muted`, `subtle` |
| Accent | `accent`, `accent_hover`, `on_accent`; brand tricolour `brand_green`, `brand_white`, `brand_red` |
| States | `success`, `warning`, `danger`, `info`; plus `focus`, `selection`, `log_bg`, `log_text` |

- `tests/test_sitdown_ui.py` checks WCAG contrast for both themes: body text ≥ 7:1, secondary text and every state colour ≥ 4.5:1, white on the accent ≥ 4.5:1.
- Legacy names (`THEME['background']`, `panel`, `primary`, `connected`, `highlight`) still map to these roles.

**Type** (named Tk fonts, so a size change updates every widget live):

| Style | Size (pt) | Weight |
| --- | --- | --- |
| `display` | 22 | bold |
| `title` | 17 | bold |
| `heading` | 13 | bold |
| `body` | 12 | regular |
| `small` | 11 | regular |
| `mono` (Menlo) | 12 | regular |
| `button` | 12 | bold |
| `nav` | 12 | regular |

Settings → General → Text size scales them by 0.9, 1.0, 1.15 or 1.3.

**Spacing:** `xs` 4, `sm` 8, `md` 12, `lg` 16, `xl` 24, `xxl` 32.

**Motion:** none. Hover and focus changes are instant, so reduced-motion preferences are respected by design.

## Components

- `button(kind=primary|secondary|danger|ghost, small)`: keyboard focusable (Tab, then Space or Return) with a visible focus ring and a hover state.
- `card(title, subtitle, actions)`: a surface with a title row and optional small actions.
- `pill(variable)`: a symbol, a colour and the status words, from `state_of()`: ● ok, ◐ busy, ▲ warning, ■ error, ○ idle.
- `toggle(title, variable, help)`: a switch that also says On/Off in words; Space or Return flips it.
- `segmented(variable, options)`: mutually exclusive choices; the selected one is filled and ticked.
- `field(title, variable, help, secret)`: a label and help text on the left, the input on the right.
- `notice(kind)`: an inline info, success, warning or error line with an icon.
- `empty_state(title, body, action)`: what to do when there is nothing to show yet.
- `text_view()`: a read-only scrolling text area; timestamps are muted, your own messages are tinted, failures are red.
- `show_info()`: a themed, non-blocking dialog whose accent and icon follow success, warning or error.

## Navigation

| Group | Pages |
| --- | --- |
| Overview | Dashboard, Radio |
| Mesh | Chat, Nodes, Network |
| BBS | Mail, Boards, Broadcast |
| App | Settings, About |

- Every page has a header with its title and a one-line purpose, plus the server status and a Start/Stop server button.
- A status bar along the bottom shows radio link, MQTT and uptime.
- Shortcuts: ⌘1–⌘9 for pages, ⌘, for Settings, ⌘F to search the log.
- Stopping a live server asks for confirmation.

## Screens

| Screen | What it holds |
| --- | --- |
| Dashboard | A first-run tip (dismissible); three cards (BBS server, Radio connection, MQTT networks); the activity log in a resizable split |
| Radio | Connection state and channel list (previously raw JSON) |
| Chat | Allow-sending switch, New direct message, Clear history; tabs per channel, person and MQTT channel, each with Send and a byte counter |
| Nodes | Search with a live result count |
| Network | Broker status and Connect/Disconnect; per-network packet feeds; the server-side counter |
| Mail, Boards | Records as titled entries with sender and date, plus empty states |
| Broadcast | Announcement (byte counter, preview and confirm), Weather desk, and the radio command list with Copy buttons (replaces the old pop-up) |
| Settings | Tabs: General, Startup, Admins, Weather, CentralNJ, SJMesh, Personality, Advanced. Fields have plain labels and help text; networks are grouped into Connection / Channels / Sending; one Save changes bar |
| About | Version, credits, notes, diagnostics and the keyboard shortcuts list |

General holds theme, text size, the sending switch, backups and troubleshooting.

## Screenshots

The Mac CI builds run `scripts/capture_desktop_preview.py` in demo mode. It saves every page in both themes as `docs/images/<theme>-<page>-preview-demo.png`, uploaded as the `sitdown-pr-desktop-preview` artifact.
