"""Кэш-бастинг статики: каждая ссылка на static в шаблоне обязана нести ?v=.

Иконка (favicon + титлбар) однажды уже подключалась без ?v= — браузер и WebView2
кэшируют её намертво, и после замены иконки пользователь продолжал видеть
прежнюю. Тест проверяет правило для всех ассетов сразу, а не только для иконки.
"""
import re
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "webapp" / "templates" / "index.html"

# {{ request.url_for('static', path='ФАЙЛ') }} + необязательный ?v={{ ... }}
STATIC_REF = re.compile(
    r"""url_for\(\s*['"]static['"]\s*,\s*path=['"](?P<file>[^'"]+)['"]\s*\)\s*\}\}(?P<suffix>\?v=\{\{)?"""
)


def _refs():
    return list(STATIC_REF.finditer(TEMPLATE.read_text(encoding="utf-8")))


def test_template_has_static_refs():
    # Страховка от «зелёного» теста при сломанном регекспе или переезде шаблона.
    files = {m.group("file") for m in _refs()}
    assert {"style.css", "app.js", "washjournal-icon.svg"} <= files


@pytest.mark.parametrize("match", _refs(), ids=lambda m: m.group("file"))
def test_every_static_ref_is_versioned(match):
    assert match.group("suffix"), (
        f"{match.group('file')} подключён без ?v= — браузер закэширует его навсегда"
    )
