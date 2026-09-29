"""Light and dark palettes plus the application's global Qt stylesheet."""
from __future__ import annotations

FONT_FAMILY = "PingFang SC, Microsoft YaHei, Segoe UI, Inter, sans-serif"
FONT_SIZE = {"h1": 20, "h2": 16, "body": 13, "small": 11}
SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
RADIUS = {"sm": 6, "md": 8, "lg": 12}

LIGHT = {
    "bg": "#FAFAFA", "surface": "#FFFFFF", "input": "#FFFFFF", "border": "#E8E8E8", "text": "#1F1F1F",
    "text_sub": "#8A8A8A", "accent": "#3B82F6", "hover": "#F0F0F0",
    "selected": "#E8F0FE", "accent_text": "#FFFFFF", "danger": "#DC2626",
    "scrollbar": "rgba(110, 110, 110, 115)", "scrollbar_hover": "rgba(90, 90, 90, 190)",
    "shadow_alpha": 34, "shadow": "rgba(0,0,0,0.06)",
}
DARK = {
    "bg": "#1A1A1A", "surface": "#242424", "input": "#2B2B2B", "border": "#333333", "text": "#EDEDED",
    "text_sub": "#A0A0A0", "accent": "#60A5FA", "hover": "#2E2E2E",
    "selected": "#1E3A5F", "accent_text": "#1A1A1A", "danger": "#F87171",
    "scrollbar": "rgba(170, 170, 170, 100)", "scrollbar_hover": "rgba(195, 195, 195, 190)",
    "shadow_alpha": 82, "shadow": "rgba(0,0,0,0.4)",
}


def get_colors(theme: str = "light") -> dict[str, str]:
    """Return the shared color tokens used by widgets and custom painting."""
    return DARK if theme == "dark" else LIGHT


def get_stylesheet(
    theme: str = "light",
    font_family: str = FONT_FAMILY,
    font_size: int = 13,
) -> str:
    """Build themed QSS using one global family and scalable type sizes."""
    colors = get_colors(theme)
    font_size = max(10, min(24, int(font_size)))
    brand_size = round(font_size * 1.15)
    page_title_size = round(font_size * 1.62)
    section_size = round(font_size * 0.92)
    small_size = max(10, round(font_size * 0.85))
    font_family = font_family.replace('"', "")
    family_css = font_family if "," in font_family else f'"{font_family}", sans-serif'
    return f"""
    * {{ font-family: {family_css}; font-size: {font_size}px; color: {colors['text']}; }}
    QMainWindow, QWidget#AppRoot {{ background: {colors['bg']}; }}
    QDialog, QMessageBox {{ background: {colors['bg']}; }}
    QGroupBox {{ background: {colors['surface']}; border: 1px solid {colors['border']};
        border-radius: 8px; margin-top: 12px; padding: 12px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
    QWidget#Sidebar, QWidget#DetailPanel {{ background: {colors['surface']}; }}
    QWidget#Sidebar {{ border-right: 1px solid {colors['border']}; }}
    QWidget#DetailPanel {{ border-left: 1px solid {colors['border']}; }}
    QScrollArea#DetailScroll, QScrollArea#DetailScroll QWidget#qt_scrollarea_viewport,
    QWidget#DetailContent {{ background: {colors['surface']}; color: {colors['text']}; border: 0; }}
    QWidget#DetailContent QLabel {{ background: transparent; color: {colors['text']}; }}
    QWidget#DetailContent QLabel#Muted, QWidget#DetailContent QLabel#SectionTitle {{ color: {colors['text_sub']}; }}
    QWidget#DetailContent QPushButton:disabled {{ color: {colors['text_sub']}; }}
    QFrame#BookCard {{ background: {colors['surface']}; border: 1px solid {colors['border']}; border-radius: 11px; margin: 2px; }}
    QFrame#BookCard:hover {{ border-color: {colors['accent']}; background: {colors['hover']}; }}
    QLabel#BookCover, QLabel#DetailCover {{ background: {colors['selected']}; color: {colors['accent']};
        border-radius: 8px; font-size: 42px; font-weight: 700; }}
    QLabel#CoverInitial {{ background: transparent; color: {colors['accent']}; font-size: 48px; font-weight: 700; }}
    QLabel#CoverCategoryIcon {{ background: transparent; color: {colors['text_sub']}; font-size: 21px; }}
    QLabel#CardTitle {{ font-weight: 650; }}
    QLabel#CardChips {{ color: {colors['accent']}; font-size: {small_size}px; }}
    QPushButton#FavoriteButton {{ background: {colors['surface']}; color: {colors['accent']};
        border: 1px solid {colors['border']}; border-radius: 8px; padding: 2px; font-size: 17px; }}
    QPushButton#CardOpen {{ background: {colors['accent']}; color: {colors['accent_text']}; border-radius: 7px; font-weight: 600; }}
    QPushButton#CardEdit {{ background: {colors['surface']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 7px; }}
    QFrame#Toolbar, QFrame#Footer {{ background: {colors['bg']}; border: 0; }}
    QLabel#AppBrand {{ font-size: {brand_size}px; font-weight: 700; letter-spacing: 1px; }}
    QLabel#PageTitle {{ font-size: {page_title_size}px; font-weight: 700; }}
    QLabel#SectionTitle {{ font-size: {section_size}px; font-weight: 700; color: {colors['text_sub']}; }}
    QLabel#Muted {{ color: {colors['text_sub']}; }}
    QLineEdit, QComboBox, QSpinBox, QTextEdit {{
        background: {colors['input']}; border: 1px solid {colors['border']};
        border-radius: 8px; padding: 8px 10px; selection-background-color: {colors['selected']};
    }}
    QAbstractItemView {{ background: {colors['surface']}; color: {colors['text']};
        border: 1px solid {colors['border']}; selection-background-color: {colors['selected']}; }}
    QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px 1px; }}
    QScrollBar:horizontal {{ background: transparent; height: 7px; margin: 1px 2px; }}
    QScrollBar::handle:vertical {{ background: {colors['scrollbar']}; min-height: 26px; border-radius: 3px; }}
    QScrollBar::handle:horizontal {{ background: {colors['scrollbar']}; min-width: 26px; border-radius: 3px; }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
        background: {colors['scrollbar_hover']};
    }}
    QScrollBar[scrolling="false"]::handle:vertical,
    QScrollBar[scrolling="false"]::handle:horizontal {{ background: transparent; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: 0; background: transparent; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QMenu {{ background: {colors['surface']}; color: {colors['text']}; border: 1px solid {colors['border']};
        padding: 5px; }}
    QMenu::item {{ padding: 7px 26px 7px 10px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {colors['selected']}; color: {colors['text']}; }}
    QMenu::separator {{ height: 1px; background: {colors['border']}; margin: 4px 6px; }}
    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px;
        background: {colors['input']}; border: 1px solid {colors['border']}; }}
    QCheckBox::indicator {{ border-radius: 4px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {colors['accent']}; border-color: {colors['accent']}; }}
    QLineEdit:focus, QTextEdit:focus {{ border: 1px solid {colors['accent']}; }}
    QPushButton {{ background: transparent; border: 0; border-radius: 7px; padding: 7px 10px; }}
    QPushButton:hover {{ background: {colors['hover']}; }}
    QPushButton#Primary {{ background: {colors['accent']}; color: {colors['accent_text']}; font-weight: 600; }}
    QPushButton#Primary:hover {{ opacity: 0.9; }}
    QPushButton#Quiet {{ color: {colors['text_sub']}; }}
    QPushButton#DetailToggle:checked {{
        background: {colors['selected']}; color: {colors['accent']};
    }}
    QPushButton#Danger {{ color: {colors['danger']}; border: 1px solid {colors['border']}; }}
    QPushButton#Danger:hover {{ background: {colors['hover']}; }}
    QAbstractItemView {{ alternate-background-color: {colors['hover']}; }}
    QListWidget, QTreeWidget, QTableWidget {{ background: transparent; border: 0; outline: 0; }}
    QListWidget::item, QTreeWidget::item {{ border-radius: 7px; padding: 7px 8px; margin: 1px 4px; }}
    QListWidget::item:hover, QTreeWidget::item:hover {{ background: {colors['hover']}; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{ background: {colors['selected']}; color: {colors['accent']}; }}
    QHeaderView::section {{ background: {colors['surface']}; color: {colors['text_sub']}; border: 0;
        border-bottom: 1px solid {colors['border']}; padding: 9px; font-weight: 600; }}
    QTableWidget {{ gridline-color: {colors['border']}; }}
    QTableWidget::item {{ padding: 7px; border-bottom: 1px solid {colors['border']}; }}
    QTableWidget::item:selected {{ background: {colors['selected']}; color: {colors['text']}; }}
    QSplitter::handle {{ background: {colors['border']}; width: 1px; }}
    QProgressBar {{ background: {colors['hover']}; border: 0; border-radius: 3px; height: 6px; text-align: center; }}
    QProgressBar::chunk {{ background: {colors['accent']}; border-radius: 3px; }}
    QToolTip {{ background: {colors['surface']}; color: {colors['text']}; border: 1px solid {colors['border']}; padding: 6px; }}
    """


APP_STYLE = get_stylesheet("light")


