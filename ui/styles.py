PALETTE = {
    "base": "#14161b",
    "panel": "#1e222a",
    "input": "#272c35",
    "border": "#333b48",
    "accent": "#ff7a5c",
    "accent_hover": "#ff9377",
    "accent_text": "#241009",
    "text": "#f3f5f8",
    "secondary": "#ccd3db",
    "muted": "#949da8",
    "ready": "#3ddc8f",
}


def app_stylesheet() -> str:
    p = PALETTE
    return f"""
    * {{
        font-family: "Inter", "Noto Sans", sans-serif;
        font-size: 12px;
        color: {p['text']};
    }}
    QMainWindow, QDialog, QMessageBox, QWidget#root, QStackedWidget {{ background: {p['base']}; }}
    QMenuBar {{
        background: #11151a;
        border-bottom: 1px solid {p['border']};
        padding: 1px 5px;
        spacing: 3px;
    }}
    QMenuBar::item {{ padding: 3px 8px; background: transparent; }}
    QMenuBar::item:selected {{ background: {p['input']}; border-radius: 3px; }}
    QMenu {{ background: {p['panel']}; border: 1px solid {p['border']}; padding: 5px; }}
    QMenu::item {{ padding: 6px 28px 6px 12px; }}
    QMenu::item:selected {{ background: {p['input']}; }}
    QToolBar {{
        background: #151a20;
        border: none;
        border-bottom: 1px solid {p['border']};
        spacing: 2px;
        padding: 2px 5px;
    }}
    QToolBar::separator {{ background: {p['border']}; width: 1px; margin: 3px 5px; }}
    QToolButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 3px;
        padding: 3px 5px;
    }}
    QToolButton:hover {{ background: {p['input']}; border-color: {p['border']}; }}
    QToolButton:pressed {{ background: #303641; }}
    QToolBar QToolButton:checked {{
        background: {p['input']};
        border-color: {p['border']};
        color: {p['accent']};
    }}
    QTabBar#pageTabs {{ background: transparent; }}
    QTabBar#pageTabs::tab {{
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        padding: 6px 11px 5px 9px;
        margin: 0 1px;
        color: {p['secondary']};
    }}
    QTabBar#pageTabs::tab:hover {{ background: {p['input']}; }}
    QTabBar#pageTabs::tab:selected {{
        background: {p['input']};
        border-bottom-color: {p['accent']};
        color: {p['text']};
    }}
    QStatusBar {{
        background: #14191f;
        border-top: 1px solid {p['border']};
        color: {p['secondary']};
    }}
    QStatusBar::item {{ border: none; }}
    QSplitter::handle {{ background: {p['border']}; width: 1px; }}
    QWidget#videoSurface {{ background: #080a0d; border: 1px solid #242a33; }}
    QWidget#transportBar {{ background: #151a20; border-top: 1px solid #252b34; }}
    QToolButton#transport {{
        background: {p['panel']};
        border: 1px solid {p['border']};
        border-radius: 4px;
        min-width: 34px;
        min-height: 30px;
        padding: 0;
    }}
    QToolButton#transport:hover {{ background: {p['input']}; border-color: #46505f; }}
    QToolButton#marker {{
        color: {p['accent']};
        background: {p['panel']};
        border: 1px solid {p['border']};
        border-radius: 4px;
        font-size: 15px;
        font-weight: 700;
        min-width: 34px;
        min-height: 30px;
        padding: 0;
    }}
    QToolButton#addRange {{
        color: {p['secondary']};
        background: {p['input']};
        border: 1px solid {p['border']};
        border-radius: 3px;
        padding: 3px 6px;
        font-size: 10px;
    }}
    QToolButton#addRange:hover {{ border-color: {p['accent']}; color: {p['text']}; }}
    QToolButton#paneToggle, QToolButton#paneRestore {{
        background: {p['input']};
        border: 1px solid {p['border']};
        border-radius: 3px;
        padding: 1px;
        min-width: 20px;
        min-height: 22px;
        max-width: 20px;
        max-height: 22px;
    }}
    QToolButton#volume {{
        background: transparent;
        border: none;
        padding: 3px;
    }}
    QSlider#volume::groove:vertical {{
        background: {p['border']};
        width: 4px;
        border-radius: 2px;
    }}
    QSlider#volume::sub-page:vertical {{ background: {p['input']}; border-radius: 2px; }}
    QSlider#volume::add-page:vertical {{ background: {p['accent']}; border-radius: 2px; }}
    QSlider#volume::handle:vertical {{
        background: {p['secondary']};
        border: 1px solid {p['border']};
        height: 10px;
        margin: 0 -4px;
        border-radius: 5px;
    }}
    QLabel#timecode {{ font-family: "DejaVu Sans Mono"; font-size: 13px; color: {p['secondary']}; }}
    QLabel#markerTime {{ font-family: "DejaVu Sans Mono"; font-size: 11px; color: {p['muted']}; }}
    QWidget#sideContainer {{ background: #171c22; border-left: 1px solid {p['border']}; }}
    QWidget#rightPanel {{ background: #171c22; }}
    QLabel#sectionTitle {{ font-weight: 700; font-size: 13px; color: {p['secondary']}; }}
    QListView {{
        background: transparent;
        border: none;
        outline: none;
        selection-background-color: #2a3038;
    }}
    QListView::item {{ border-bottom: 1px solid {p['border']}; }}
    QComboBox {{
        background: {p['panel']};
        border: 1px solid {p['border']};
        border-radius: 4px;
        padding: 5px 7px;
        color: {p['secondary']};
    }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: {p['panel']};
        border: 1px solid {p['border']};
        selection-background-color: {p['input']};
        selection-color: {p['text']};
    }}
    QLineEdit {{
        background: {p['panel']};
        border: 1px solid {p['border']};
        border-radius: 3px;
        padding: 5px 7px;
        color: {p['secondary']};
        selection-background-color: {p['accent']};
        selection-color: {p['accent_text']};
    }}
    QLineEdit:read-only {{ color: {p['muted']}; }}
    QPushButton {{
        background: {p['input']};
        border: 1px solid {p['border']};
        border-radius: 3px;
        padding: 5px 9px;
        color: {p['secondary']};
    }}
    QPushButton:hover {{ background: #303641; border-color: #46505f; color: {p['text']}; }}
    QPushButton#primary {{
        background: {p['accent']};
        color: {p['accent_text']};
        border: none;
        border-radius: 4px;
        font-weight: 700;
        padding: 7px 13px;
    }}
    QPushButton#primary:hover {{ background: {p['accent_hover']}; }}
    QPushButton#targetAction {{
        background: #2a211f;
        border: 1px solid #70483e;
        color: {p['accent_hover']};
        font-weight: 700;
    }}
    QPushButton#targetAction:hover {{ background: #332520; border-color: {p['accent']}; }}
    QPushButton#primaryCompact {{
        background: {p['accent']};
        color: {p['accent_text']};
        border: none;
        font-weight: 700;
        padding: 5px 9px;
    }}
    QToolButton#referenceAdd {{
        background: {p['input']};
        border: 1px solid {p['border']};
        border-radius: 3px;
        padding: 4px 8px;
        color: {p['secondary']};
    }}
    QToolButton#referenceAdd:hover {{ border-color: {p['accent']}; color: {p['text']}; }}
    QToolButton#compactMenu {{
        background: transparent;
        border: 1px solid transparent;
        min-width: 24px;
        padding: 3px;
        font-size: 16px;
        font-weight: 700;
    }}
    QToolButton#compactMenu:hover {{ background: {p['input']}; border-color: {p['border']}; }}
    QLabel#placeholderTitle {{ font-size: 20px; font-weight: 700; color: {p['secondary']}; }}
    QLabel#placeholderBody {{ color: {p['muted']}; }}
    QLabel#pageTitle {{ font-size: 19px; font-weight: 700; color: {p['text']}; }}
    QLabel#pageSubtitle {{ color: {p['muted']}; }}
    QLabel#engineStatus {{ color: {p['secondary']}; }}
    QLabel#batchSummary {{ color: {p['muted']}; font-family: "DejaVu Sans Mono"; }}
    QWidget#batchInspector {{ background: #171c22; border-left: 1px solid {p['border']}; }}
    QTableWidget#batchQueue {{
        background: #101318;
        alternate-background-color: #14191f;
        border: 1px solid {p['border']};
        gridline-color: #252b34;
        outline: none;
        selection-background-color: {p['input']};
    }}
    QTableWidget#batchQueue::item {{ padding: 4px 6px; }}
    QTableWidget#batchQueue QHeaderView::section {{
        background: #181d24;
        border: none;
        border-right: 1px solid {p['border']};
        border-bottom: 1px solid {p['border']};
        color: {p['muted']};
        font-weight: 700;
        padding: 5px 6px;
    }}
    QListWidget#batchCharacters {{
        background: #101318;
        border: 1px solid {p['border']};
        outline: none;
    }}
    QListWidget#batchCharacters::item {{ padding: 5px 7px; border-bottom: 1px solid #252b34; }}
    QListWidget#batchCharacters::item:selected {{ background: {p['input']}; }}
    QToolButton#whoCell {{
        background: transparent; border: none; padding: 2px 6px; color: {p['secondary']}; text-align: left;
    }}
    QToolButton#whoCell:hover {{ background: {p['input']}; border-radius: 3px; }}
    QToolButton#whoCell[missing="true"] {{ color: {p['accent']}; }}
    QToolButton#whoCell::menu-indicator {{ image: none; width: 0px; }}
    QListWidget#batchCharacters::indicator {{
        width: 13px; height: 13px; border: 1px solid #5a6472; border-radius: 3px; background: #14181e;
    }}
    QListWidget#batchCharacters::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; }}
    QProgressBar {{
        background: #101318;
        border: 1px solid {p['border']};
        border-radius: 3px;
        color: {p['secondary']};
        text-align: center;
        min-height: 15px;
    }}
    QProgressBar::chunk {{ background: {p['accent']}; border-radius: 2px; }}
    QLabel#warningBanner {{
        background: #2b201c;
        border: 1px solid #6b4236;
        border-radius: 4px;
        color: {p['accent_hover']};
        padding: 7px;
    }}
    QLabel#scopeBadge {{
        background: {p['input']};
        border: 1px solid {p['border']};
        border-radius: 3px;
        color: {p['accent']};
        font-size: 10px;
        font-weight: 700;
        padding: 2px 5px;
    }}
    QLabel#referenceHint, QLabel#targetHint {{ color: {p['muted']}; font-size: 10px; }}
    QLabel#settingsValue {{
        color: {p['accent']};
        font-family: "DejaVu Sans Mono";
        min-width: 34px;
    }}
    QLabel#targetThumbnail {{
        background: #101318;
        border: 1px solid {p['border']};
        border-radius: 3px;
    }}
    QListWidget#characterList, QListWidget#referenceList {{
        background: #11151a;
        border: 1px solid {p['border']};
        outline: none;
    }}
    QListWidget#characterList::item {{ padding: 7px; border-bottom: 1px solid #252b34; }}
    QListWidget#characterList::item:selected, QListWidget#referenceList::item:selected {{
        background: {p['input']};
        color: {p['text']};
    }}
    QSlider::groove:horizontal {{
        background: {p['border']};
        height: 4px;
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {p['secondary']};
        border: 1px solid {p['border']};
        width: 12px;
        margin: -5px 0;
        border-radius: 6px;
    }}
    QWidget#filterManager {{ background: #171c22; }}
    QWidget#filterInspector {{ background: #171c22; border-left: 1px solid {p['border']}; }}
    QTabWidget#filterTabs::pane {{ border: 1px solid {p['border']}; background: #151a20; }}
    QTabWidget#filterTabs QTabBar::tab {{
        background: #151a20;
        border: 1px solid {p['border']};
        border-bottom: none;
        padding: 5px 10px;
        color: {p['muted']};
    }}
    QTabWidget#filterTabs QTabBar::tab:selected {{ background: {p['input']}; color: {p['text']}; }}
    QListWidget#filterList {{
        background: #11151a;
        border: 1px solid {p['border']};
        outline: none;
    }}
    QListWidget#filterList::item {{ padding: 5px 7px; border-bottom: 1px solid #252b34; }}
    QListWidget#filterList::item:selected {{ background: {p['input']}; color: {p['text']}; }}
    QGroupBox {{
        border: 1px solid {p['border']};
        border-radius: 4px;
        margin-top: 9px;
        padding-top: 6px;
        font-weight: 700;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 7px; padding: 0 4px; color: {p['secondary']}; }}
    QLabel#filterHint {{ color: {p['muted']}; font-size: 10px; }}
    QLabel#cropOutput {{ color: {p['accent']}; font-weight: 700; }}
    QSpinBox, QDoubleSpinBox {{
        background: {p['panel']};
        border: 1px solid {p['border']};
        border-radius: 3px;
        padding: 4px 6px;
    }}
    """
