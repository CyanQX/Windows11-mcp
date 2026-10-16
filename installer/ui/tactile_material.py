"""Five independently tunable vector layers for cool, substantial glass surfaces."""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class TactileTokens:
    edge: str = "#83939B"
    shoulder: str = "#5F7079"
    core: str = "#596C77"
    text: str = "#F1F6F8"
    rim_light: str = "#E7F9FF"
    rim_soft: str = "#BDD9E3"
    rim_dark: str = "#30434E"
    focus: str = "#164668"
    ambient_alpha: int = 24
    contact_alpha: int = 28
    highlight_alpha: int = 32
    rim_width: float = 1.3
    bevel_width: float = 1.1
    press_distance: float = 1.5
    motion_ms: int = 150
    press_ms: int = 90
    texture_alpha: int = 2


def manager_theme():
    app = QApplication.instance()
    return app.property("managerTheme") if app else None


def tokens_for(tone="neutral"):
    current = manager_theme()
    mode = current.mode if current else "light"
    tokens = TactileTokens()
    if mode == "warm":
        tokens = replace(tokens, edge="#92988A", shoulder="#676D5D", core="#5D6657", rim_soft="#E0E4D6", focus="#2C4E39")
    elif mode == "dark":
        tokens = replace(tokens, edge="#607683", shoulder="#415867", core="#354D5D", focus="#BFE8FF")
    if tone == "primary":
        tokens = replace(tokens, edge="#7793A6", shoulder="#4C687B", core="#405D72")
    return tokens


def rounded_path(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def alpha(value, opacity):
    color = QColor(value)
    color.setAlpha(max(0, min(255, round(opacity))))
    return color


@lru_cache(maxsize=4)
def microtexture(opacity):
    # A tiny, deterministic surface cache; no reference-image pixels are used.
    image = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    seed = 173
    for y in range(64):
        for x in range(64):
            seed = (1664525 * seed + 1013904223) & 0xFFFFFFFF
            shade = 255 if (seed >> 12) & 1 else 0
            image.setPixelColor(x, y, QColor(shade, shade, shade, (seed >> 24) % (opacity + 1)))
    return QPixmap.fromImage(image)


def paint_glass(painter, rect: QRectF, radius: float, tokens: TactileTokens, *, hover=0.0, press=0.0, enabled=True, focus=False, shadow=True, texture=True, motion_scale=1.0):
    """Paint material, specular, refractive bevel, contact/depth and microtexture."""
    if not enabled:
        tokens = replace(tokens, edge="#C5CFD5", shoulder="#B7C3CA", core="#B7C3CA", text="#344852", rim_light="#DCE5E8", rim_dark="#84959F", highlight_alpha=12, ambient_alpha=7, contact_alpha=8)
        hover = press = 0.0
    body = rect.translated(0, tokens.press_distance * press * motion_scale)
    path = rounded_path(body, radius)

    # Depth/contact: two spatially different shadows, kept inside widget margins.
    if shadow:
        painter.setPen(Qt.PenStyle.NoPen)
        for spread in range(7, -1, -1):
            bounds = rect.adjusted(-spread * .6, -spread * .5, spread * .6, spread * .6).translated(1, 2.7 - press * 1.6)
            painter.setBrush(alpha(tokens.rim_dark, tokens.ambient_alpha / 7 * (1 - press * .4)))
            painter.drawRoundedRect(bounds, radius + spread * .5, radius + spread * .5)
        painter.setBrush(alpha(tokens.rim_dark, tokens.contact_alpha * (1 - press * .65)))
        painter.drawRoundedRect(rect.translated(.6, 1.5), radius, radius)

    painter.save()
    painter.setClipPath(path)
    # Base: the readable central plane stays darker than the silver edge.
    material = QLinearGradient(body.topLeft(), body.bottomLeft())
    for position, value in ((0, tokens.edge), (.18, tokens.shoulder), (.46, tokens.core), (.66, tokens.core), (.80, tokens.shoulder), (1, tokens.edge)):
        material.setColorAt(position, QColor(value).lighter(round(100 + 1.5 * hover - 1.5 * press)))
    painter.fillPath(path, material)

    # Specular: curved upper-left reflection, fading before it meets the label.
    specular = QLinearGradient(body.topLeft(), body.bottomLeft())
    intensity = tokens.highlight_alpha * (1 + hover * .2) * (1 - press * .5)
    specular.setColorAt(0, alpha(tokens.rim_light, intensity))
    specular.setColorAt(.08, alpha(tokens.rim_light, intensity * .35))
    specular.setColorAt(.26, alpha(tokens.rim_light, 0))
    specular.setColorAt(1, alpha(tokens.rim_light, 0))
    painter.fillPath(path, specular)

    # Bevel: a different inner/outer profile, with localized top and lower glints.
    inset = QLinearGradient(body.topLeft(), body.bottomLeft())
    for position, opacity in ((0, 36 + press * 34), (.15, 4), (.55, 0), (.88, 5), (1, 32)):
        inset.setColorAt(position, alpha(tokens.rim_dark, opacity))
    painter.setPen(QPen(inset, 3.4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(body.adjusted(2.0, 2.0, -2.0, -2.0), max(0, radius - 2), max(0, radius - 2))
    if texture and tokens.texture_alpha:
        painter.drawTiledPixmap(body, microtexture(tokens.texture_alpha))
    painter.restore()

    rim = QLinearGradient(body.topLeft(), body.bottomRight())
    for position, value, opacity in ((0, tokens.rim_dark, 180), (.17, tokens.rim_light, 235), (.43, tokens.rim_dark, 150), (.69, tokens.rim_dark, 120), (.90, tokens.rim_light, 245), (1, tokens.rim_dark, 130)):
        rim.setColorAt(position, alpha(value, opacity))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(rim, tokens.rim_width))
    painter.drawPath(path)
    light = QLinearGradient(body.topLeft(), body.bottomLeft())
    for position, opacity in ((0, 245), (.20, 100), (.48, 6), (.70, 15), (.96, 235), (1, 180)):
        light.setColorAt(position, alpha(tokens.rim_soft if .15 <= position <= .8 else tokens.rim_light, opacity * (1 - press * .25)))
    painter.setPen(QPen(light, tokens.bevel_width))
    painter.drawRoundedRect(body.adjusted(1.5, 1.5, -1.5, -1.5), max(0, radius - 1.5), max(0, radius - 1.5))
    if focus:
        painter.setPen(QPen(QColor(tokens.focus), 2.0))
        painter.drawRoundedRect(rect.adjusted(-3.5, -3.5, 3.5, 3.5), radius + 3.5, radius + 3.5)


def paint_panel(painter, rect, radius, *, popup=False, selected=False):
    current = manager_theme()
    if current is None:
        return
    colors = current.colors
    path = rounded_path(rect, radius)
    fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
    fill.setColorAt(0, QColor(colors["glass"]).lighter(103))
    fill.setColorAt(.55, QColor(colors["glass"]))
    fill.setColorAt(1, QColor(colors["bg"]))
    painter.fillPath(path, fill)
    # Pale panels use the same directional silver bevel without dark button fill.
    rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
    rim.setColorAt(0, QColor(colors["border"]))
    rim.setColorAt(.18, alpha("#FFFFFF", 230 if current.mode != "dark" else 95))
    rim.setColorAt(.58, QColor(colors["accent"] if selected or popup else colors["border"]))
    rim.setColorAt(1, QColor(colors["border"]))
    painter.setPen(QPen(rim, 1.35 if popup or selected else 1))
    painter.drawPath(path)
    painter.setPen(QPen(alpha("#E7F9FF", 90 if current.mode != "dark" else 35), 1))
    painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), radius - 1.5, radius - 1.5)
