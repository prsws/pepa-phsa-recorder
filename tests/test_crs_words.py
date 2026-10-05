"""The app's verbs are Create, Read, Supersede. This file checks the source."""
import io
import pathlib
import re
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_FILES = sorted((ROOT / "app").glob("*.py"))
TEMPLATE_FILES = sorted((ROOT / "templates").glob("*.html"))

WORDS = re.compile(r"(edit|updat|delet|truncat)", re.IGNORECASE)
PERMITTED = re.compile(
    r"update\s+phsa\.\w+\s+set\s+superseded_by\s*=", re.IGNORECASE
)
_SKIP = (tokenize.NL, tokenize.COMMENT)
_LINE_START = (
    tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING,
)
_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)


def _violations(source):
    """Return one text per banned word found in code.

    Comments and docstrings are skipped. In other strings the permitted
    supersede statement is removed first, then the words are looked for.
    """
    found = []
    previous = tokenize.ENCODING
    readline = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(readline):
        if tok.type in _SKIP:
            continue
        if tok.type == tokenize.NAME:
            if WORDS.search(tok.string):
                found.append(f"line {tok.start[0]}: name {tok.string}")
        elif tok.type == tokenize.STRING or tok.type == _FSTRING_MIDDLE:
            is_docstring = (
                tok.type == tokenize.STRING and previous in _LINE_START
            )
            if not is_docstring:
                rest = PERMITTED.sub("", tok.string)
                if WORDS.search(rest):
                    found.append(f"line {tok.start[0]}: string")
        previous = tok.type
    return found


def test_checker_flags_function_name():
    source = "def update_thing():\n    pass\n"
    found = _violations(source)
    assert found == ["line 1: name update_thing"], found


def test_checker_flags_method_call():
    source = "d = {}\nd.update({})\n"
    found = _violations(source)
    assert found == ["line 2: name update"], found


def test_checker_flags_delete_statement():
    source = 'x = "DELETE FROM phsa.condition"\n'
    found = _violations(source)
    assert found == ["line 1: string"], found


def test_checker_flags_other_update_statement():
    source = 'x = "UPDATE phsa.condition SET text = %s"\n'
    found = _violations(source)
    assert found == ["line 1: string"], found


def test_checker_allows_comment_docstring_and_permitted_statement():
    assert _violations(
        'def f():\n    """No Update and no Delete."""\n    return 1  # never edit\n'
    ) == []
    assert _violations('"""Module text: no Update."""\nx = 1\n') == []
    assert _violations(
        'x = "UPDATE phsa.condition SET superseded_by = %(new)s"\n'
    ) == []


def test_app_python_sources_are_clean():
    assert APP_FILES, "app/ has no .py files"
    for path in APP_FILES:
        found = _violations(path.read_text())
        assert found == [], f"{path.name}: {found}"


def test_permitted_statement_only_in_queries():
    for path in APP_FILES:
        count = len(PERMITTED.findall(path.read_text()))
        if path.name == "queries.py":
            assert count == 3, f"{path.name}: found {count} permitted statements"
        else:
            assert count == 0, f"{path.name}: found {count} permitted statements"


def test_templates_are_clean():
    assert TEMPLATE_FILES, "templates/ has no .html files"
    for path in TEMPLATE_FILES:
        text = path.read_text()
        assert WORDS.search(text) is None, f"{path.name}: banned word found"


def test_routes_are_clean(app):
    rules = list(app.url_map.iter_rules())
    assert len(rules) > 5
    for rule in rules:
        assert WORDS.search(rule.rule) is None, f"route {rule.rule}"
        assert WORDS.search(rule.endpoint) is None, f"endpoint {rule.endpoint}"

