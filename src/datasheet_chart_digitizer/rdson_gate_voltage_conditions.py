"""Owned condition evidence for RDS(VGS), independent of trace geometry."""

from __future__ import annotations

import re

from .find_charts import group_words_into_lines, line_text


_TYPICAL_NOTE = re.compile(
    r"Typical\s+Characteristics\s*\(\s*T\s*(?P<sub>[JCA])\s*=\s*"
    r"(?P<value>[+-]?\d{1,3})\s*(?:[°º]\s*C|℃)\s+Noted\s*\)", re.I)


def typical_temperature_note(page):
    """Only the explicitly chart-scoped heading on THIS page can supply T."""
    text = " ".join(page.get_text("text").split())
    return [(float(m['value']), 'T' + m['sub'].lower(), m.group(0)) for m in _TYPICAL_NOTE.finditer(text)]


def pulse_conditions(words, transform, plot, labels):
    """Read pulse regime inside this frame, never from a neighbouring plot."""
    local = []
    for word in words.words:
        x, y = transform.to_px((word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2)
        if plot.x0 <= x <= plot.x1 and plot.y0 <= y <= plot.y1:
            local.append(word)
    sources = [(line_text(line), "text_layer_or_locator") for line in group_words_into_lines(local)]
    sources += [(l.text, "plot_ocr") for l in labels
                if plot.x0 <= l.cx <= plot.x1 and plot.y0 <= l.cy <= plot.y1]
    found = []
    for text, source in sources:
        duration = re.search(r"PULSE\s+DURATION\s*=\s*([\d.]+)\s*[µμu]s", text, re.I)
        duty = re.search(r"DUTY\s+CYCLE\s*=\s*([\d.]+)\s*%\s*(MAX)?", text, re.I)
        if duration or duty or re.search(r"\bPulsed\b", text, re.I):
            entry = {"regime": "pulsed", "source": source, "evidence": text}
            if duration:
                entry["duration_us"] = float(duration[1])
            if duty:
                entry["duty_cycle_percent"] = float(duty[1])
                entry["duty_cycle_is_maximum"] = bool(duty[2])
            if entry not in found:
                found.append(entry)
    return found
