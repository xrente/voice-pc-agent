"""
Тесты preprocess_text из tts.py.

Проверяют замену латиницы на кириллицу — это ключевое решение
для корректного произношения «YouTube», «Python», «API» и т.п.
"""

from tts import preprocess_text


def test_youtube_replaced():
    assert "ютуб" in preprocess_text("открой youtube").lower()


def test_python_replaced():
    assert "питон" in preprocess_text("Python для начинающих").lower()


def test_case_insensitive():
    out = preprocess_text("YouTube и YOUTUBE").lower()
    assert out.count("ютуб") == 2


def test_api_replaced():
    assert "а пи ай" in preprocess_text("используй API").lower()


def test_multi_word_replacement():
    out = preprocess_text("открой vs code").lower()
    assert "ви эс код" in out


def test_unknown_latin_spelled_letter_by_letter():
    """Незнакомое латинское слово произносится по буквам."""
    out = preprocess_text("xyz")
    assert out == "X Y Z"


def test_cyrillic_unchanged():
    text = "создай папку отчёты"
    assert preprocess_text(text) == text


def test_empty_string():
    assert preprocess_text("") == ""


def test_mixed_text():
    out = preprocess_text("найди python на youtube").lower()
    assert "питон" in out
    assert "ютуб" in out
    assert "найди" in out
    assert "на" in out