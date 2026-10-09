"""Theme palette and stylesheet, shared by both apps.

Light is the base, a prefers-color-scheme block covers the window before
Streamlit reports a theme, and Streamlit's own resolved theme is emitted last so
the in-app Appearance setting wins.
"""

import streamlit as st


def resolve_theme():
    """The app's active theme, or None when Streamlit has not reported one yet.

    st.context.theme accounts for the Appearance setting and, when that is set
    to "Use system setting", the browser/OS preference. It reads None before a
    browser session exists, so callers must handle "unknown" rather than
    assuming a side - see build_theme_css.
    """

    try:
        theme_type = st.context.theme.type

    except Exception:
        return None

    if theme_type in ("light", "dark"):
        return theme_type

    return None


THEME_PALETTES = {

    "dark": {
        "bg-grad-1": "rgba(78, 161, 255, 0.12)",
        "bg-grad-2": "rgba(64, 195, 139, 0.10)",
        "bg-from": "#09111d",
        "bg-to": "#06101a",
        "panel-from": "rgba(15, 29, 49, 0.92)",
        "panel-to": "rgba(10, 21, 37, 0.96)",
        "panel-solid": "rgba(9, 18, 31, 0.75)",
        "sidebar-from": "#121824",
        "sidebar-to": "#0e1520",
        "border": "rgba(120, 160, 220, 0.18)",
        "border-strong": "rgba(126, 170, 255, 0.32)",
        "text": "#edf4ff",
        "text-soft": "#b3c4dc",
        "text-muted": "#7d90aa",
        "heading": "#f7fbff",
        "accent": "#4ea1ff",
        "accent-strong": "#2077ff",
        "accent-soft": "rgba(78, 161, 255, 0.18)",
        "accent-text": "#8fc2ff",
        "success": "#40c38b",
        "success-bg": "rgba(16, 42, 35, 0.55)",
        "success-fg": "#04140d",
        "success-label": "#e6fff4",
        "danger": "#ff6c6c",
        "danger-bg": "rgba(56, 16, 22, 0.6)",
        "danger-fg": "#2a0508",
        "running-bg": "rgba(14, 38, 68, 0.85)",
        "running-label": "#ffffff",
        "input-bg": "rgba(12, 24, 41, 0.9)",
        "popover-from": "rgba(8, 17, 29, 0.99)",
        "popover-to": "rgba(13, 25, 41, 0.99)",
        "card-from": "rgba(19, 36, 59, 0.96)",
        "card-to": "rgba(13, 25, 41, 0.98)",
        "inset": "rgba(255, 255, 255, 0.03)",
        "track": "rgba(120, 160, 220, 0.16)",
        "shadow": "0 22px 50px rgba(1, 8, 20, 0.45)",
        "btn-shadow": "0 10px 30px rgba(32, 119, 255, 0.28)"
    },

    "light": {
        "bg-grad-1": "rgba(32, 119, 255, 0.10)",
        "bg-grad-2": "rgba(24, 158, 106, 0.09)",
        "bg-from": "#f6f9fe",
        "bg-to": "#eef3fb",
        "panel-from": "rgba(255, 255, 255, 0.96)",
        "panel-to": "rgba(246, 249, 254, 0.98)",
        "panel-solid": "rgba(255, 255, 255, 0.9)",
        "sidebar-from": "#ffffff",
        "sidebar-to": "#f2f6fc",
        "border": "rgba(28, 62, 112, 0.16)",
        "border-strong": "rgba(28, 62, 112, 0.30)",
        "text": "#12243d",
        "text-soft": "#41567a",
        "text-muted": "#6b7f9e",
        "heading": "#0b1d34",
        "accent": "#1565d8",
        "accent-strong": "#1565d8",
        "accent-soft": "rgba(21, 101, 216, 0.12)",
        "accent-text": "#12559c",
        "success": "#1a8f60",
        "success-bg": "rgba(26, 143, 96, 0.10)",
        "success-fg": "#ffffff",
        "success-label": "#0f5b3d",
        "danger": "#c4302b",
        "danger-bg": "rgba(196, 48, 43, 0.09)",
        "danger-fg": "#ffffff",
        "running-bg": "rgba(21, 101, 216, 0.10)",
        "running-label": "#0b1d34",
        "input-bg": "#ffffff",
        "popover-from": "#ffffff",
        "popover-to": "#f7faff",
        "card-from": "#ffffff",
        "card-to": "#f4f8fe",
        "inset": "rgba(11, 29, 52, 0.04)",
        "track": "rgba(28, 62, 112, 0.12)",
        "shadow": "0 18px 44px rgba(18, 45, 85, 0.16)",
        "btn-shadow": "0 8px 22px rgba(21, 101, 216, 0.24)"
    }

}


ACTIVE_THEME = resolve_theme()

PALETTE = THEME_PALETTES[ACTIVE_THEME or "light"]


def palette_css_variables(palette, indent="        "):

    return "\n".join(
        f"{indent}--rca-{name}: {value};"
        for name, value in palette.items()
    )


def build_theme_css(active_theme):
    """Palette declarations, layered so the page is never stuck on one theme.

    Light is the base. A prefers-color-scheme block covers the case where
    Streamlit has not told us the theme yet, so the page still follows the
    browser. When Streamlit does report a theme, that palette is emitted last
    and wins - which is what makes the in-app Appearance setting work even when
    it disagrees with the OS.
    """

    blocks = [
        ":root {\n"
        + palette_css_variables(THEME_PALETTES["light"])
        + "\n    }",

        "@media (prefers-color-scheme: dark) {\n"
        "        :root {\n"
        + palette_css_variables(THEME_PALETTES["dark"], indent="            ")
        + "\n        }\n    }"
    ]

    if active_theme:
        blocks.append(
            ":root {\n"
            + palette_css_variables(THEME_PALETTES[active_theme])
            + "\n    }"
        )

    return "\n    ".join(blocks)


def inject_theme():
    """Writes the stylesheet for the active theme. Call once, near the top."""

    st.markdown(f"""
        <style>
        {build_theme_css(ACTIVE_THEME)}
        .stApp {{
            background:
                radial-gradient(circle at top left, var(--rca-bg-grad-1), transparent 28%),
                radial-gradient(circle at top right, var(--rca-bg-grad-2), transparent 24%),
                linear-gradient(180deg, var(--rca-bg-from) 0%, var(--rca-bg-to) 100%);
            color: var(--rca-text);
        }}
        [data-testid="stSidebar"] {{
            background: linear-gradient(180deg, var(--rca-sidebar-from) 0%, var(--rca-sidebar-to) 100%);
            border-right: 1px solid var(--rca-border);
        }}
        [data-testid="stSidebar"] * {{
            color: var(--rca-text);
        }}
        [data-testid="stMetric"] {{
            background: linear-gradient(180deg, var(--rca-panel-from) 0%, var(--rca-panel-to) 100%);
            border: 1px solid var(--rca-border);
            border-radius: 18px;
            padding: 1rem 1.1rem;
            box-shadow: inset 0 1px 0 var(--rca-inset);
        }}
        div[data-testid="stMetricLabel"] label,
        div[data-testid="stMetricValue"] {{
            color: var(--rca-text) !important;
        }}
        .stTextArea textarea {{
            background: var(--rca-input-bg) !important;
            color: var(--rca-text) !important;
            border: 1px solid var(--rca-border) !important;
            border-radius: 18px !important;
        }}
        .stButton > button {{
            background: linear-gradient(135deg, var(--rca-accent-strong) 0%, var(--rca-accent) 100%);
            color: #ffffff;
            border: 0;
            border-radius: 999px;
            font-weight: 700;
            padding: 0.65rem 1.25rem;
            box-shadow: var(--rca-btn-shadow);
        }}
        .stButton > button:hover {{
            filter: brightness(1.08);
        }}
        .stInfo, .stSuccess, .stWarning, .stError {{
            border-radius: 18px;
        }}
        .evidence-item {{
            position: relative;
            display: flex;
            align-items: stretch;
            gap: 0.9rem;
            margin-bottom: 1rem;
            padding: 1rem 1rem 1rem 1.15rem;
            border: 1px solid var(--rca-border);
            border-radius: 20px;
            background: linear-gradient(180deg, var(--rca-panel-from) 0%, var(--rca-panel-to) 100%);
            box-shadow: inset 0 1px 0 var(--rca-inset);
        }}
        .evidence-text {{
            flex: 1;
            min-width: 0;
            color: var(--rca-text);
            line-height: 1.62;
            font-size: 0.98rem;
        }}
        .evidence-control {{
            position: relative;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}
        .evidence-trigger {{
            width: 1.9rem;
            height: 1.9rem;
            border-radius: 999px;
            background: linear-gradient(135deg, var(--rca-accent-strong) 0%, var(--rca-accent) 100%);
            color: #ffffff;
            font-size: 0.9rem;
            font-weight: 700;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: help;
            flex-shrink: 0;
            box-shadow: var(--rca-btn-shadow);
            border: 1px solid var(--rca-border-strong);
        }}
        .evidence-popover {{
            display: none;
            position: absolute;
            top: calc(100% + 12px);
            right: 0;
            width: min(34rem, calc(100vw - 7rem));
            max-width: 34rem;
            max-height: 24rem;
            overflow-y: auto;
            z-index: 20;
            background: linear-gradient(180deg, var(--rca-popover-from) 0%, var(--rca-popover-to) 100%);
            border: 1px solid var(--rca-border-strong);
            border-radius: 20px;
            box-shadow: var(--rca-shadow);
            padding: 0.95rem;
            backdrop-filter: blur(10px);
        }}
        .evidence-control:hover .evidence-popover,
        .evidence-control:focus-within .evidence-popover {{
            display: block;
        }}
        .source-card {{
            border: 1px solid var(--rca-border);
            border-radius: 16px;
            padding: 0.9rem;
            background: linear-gradient(180deg, var(--rca-card-from) 0%, var(--rca-card-to) 100%);
            margin-bottom: 0.8rem;
            color: var(--rca-text);
            font-size: 0.92rem;
            line-height: 1.5;
            overflow-wrap: anywhere;
        }}
        .source-card:last-child {{
            margin-bottom: 0;
        }}
        .source-card-title {{
            font-weight: 700;
            color: var(--rca-heading);
            margin-bottom: 0.55rem;
        }}
        .source-card strong {{
            color: var(--rca-accent-text);
        }}
        .source-section-title {{
            margin-top: 0.7rem;
            margin-bottom: 0.45rem;
            font-weight: 700;
            color: var(--rca-text-soft);
        }}
        .source-reference {{
            border-top: 1px solid var(--rca-border);
            margin-top: 0.55rem;
            padding-top: 0.55rem;
        }}
        .source-reference-type {{
            display: inline-block;
            margin-bottom: 0.4rem;
            padding: 0.18rem 0.55rem;
            border-radius: 999px;
            background: var(--rca-accent-soft);
            color: var(--rca-accent-text);
            font-size: 0.76rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }}
        @media (max-width: 1100px) {{
            .evidence-popover {{
                top: calc(100% + 10px);
                right: -0.5rem;
                width: min(30rem, calc(100vw - 3rem));
                max-width: min(30rem, calc(100vw - 3rem));
            }}
        }}
        @media (max-width: 680px) {{
            .evidence-item {{
                align-items: flex-start;
            }}
            .evidence-popover {{
                position: fixed;
                left: 1rem;
                right: 1rem;
                top: auto;
                bottom: 1rem;
                width: auto;
                max-width: none;
                max-height: 52vh;
            }}
            .evidence-control:hover .evidence-popover,
            .evidence-control:focus-within .evidence-popover {{
                display: block;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

    return ACTIVE_THEME
