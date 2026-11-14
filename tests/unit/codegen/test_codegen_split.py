from __future__ import annotations

import ast
import textwrap

import pytest

from pytestgen.codegen import ParsedTestModule, make_free_form_tests, split_test_functions
from pytestgen.codegen.split import rename_definition
from pytestgen.domain.models import CaseOrigin
from pytestgen.errors import ExtractionError

MODULE = textwrap.dedent(
    '''\
    """Docstring is dropped."""
    import os
    from calc import add, div
    import pytest

    LIMIT = 10


    @pytest.fixture
    def acc():
        return add(1, 2)


    def _helper(x):
        return x * LIMIT


    @pytest.mark.parametrize("a, b", [(1, 2), (3, 4)])
    @pytest.mark.slow
    def test_add(a, b):
        assert add(a, b) == a + b


    async def test_async():
        assert True


    def test_uses_fixture(acc):
        assert acc == 3


    def test_uses_helper():
        assert _helper(2) == 20


    class Plain:
        pass


    class TestDiv:
        def test_zero(self):
            with pytest.raises(ZeroDivisionError):
                div(1, 0)

        def test_ok(self):
            assert div(4, 2) == 2
    '''
)


@pytest.fixture(scope="module")
def parsed() -> ParsedTestModule:
    return split_test_functions(MODULE)


def test_imports_are_collected_verbatim(parsed: ParsedTestModule) -> None:
    assert parsed.imports == ("import os", "from calc import add, div", "import pytest")


def test_helpers_keep_fixtures_constants_and_plain_classes(parsed: ParsedTestModule) -> None:
    assert [h.splitlines()[0] for h in parsed.helpers] == [
        "LIMIT = 10",
        "@pytest.fixture",
        "def _helper(x):",
        "class Plain:",
    ]


def test_tests_are_listed_in_order_with_decorators(parsed: ParsedTestModule) -> None:
    assert [name for name, _ in parsed.tests] == [
        "test_add",
        "test_async",
        "test_uses_fixture",
        "test_uses_helper",
        "TestDiv",
    ]
    add_source = dict(parsed.tests)["test_add"]
    assert add_source.splitlines()[:3] == [
        '@pytest.mark.parametrize("a, b", [(1, 2), (3, 4)])',
        "@pytest.mark.slow",
        "def test_add(a, b):",
    ]
    assert add_source.endswith("assert add(a, b) == a + b")


def test_async_test_and_class_unit_are_preserved(parsed: ParsedTestModule) -> None:
    tests = dict(parsed.tests)
    assert tests["test_async"] == "async def test_async():\n    assert True"
    assert tests["TestDiv"].startswith("class TestDiv:\n    def test_zero(self):")
    assert tests["TestDiv"].endswith("assert div(4, 2) == 2")


def test_every_piece_is_valid_python(parsed: ParsedTestModule) -> None:
    for piece in (*parsed.imports, *parsed.helpers, *(src for _, src in parsed.tests)):
        ast.parse(piece)


def test_fixture_named_like_a_test_is_a_helper() -> None:
    source = "import pytest\n\n@pytest.fixture(name='x')\ndef test_data():\n    return 1\n"
    result = split_test_functions(source)
    assert result.tests == ()
    assert len(result.helpers) == 1


def test_semicolon_separated_statements_are_split() -> None:
    result = split_test_functions("import os; import sys\nA = 1; B = 2\n")
    assert result.imports == ("import os", "import sys")
    assert result.helpers == ("A = 1", "B = 2")


def test_crlf_and_unicode_are_handled() -> None:
    source = "@d\r\ndef test_a():\r\n    assert 'привет'\r\n"
    ((name, text),) = split_test_functions(source).tests
    assert name == "test_a"
    assert "привет" in text
    assert "\r" not in text


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_plain_nodes_are_normalised_to_lf(newline: str) -> None:
    lines = [
        "import os",
        "X = [",
        "    1,",
        "]",
        "def test_a():",
        "    assert os",
        "    assert X",
        "",
    ]
    parsed = split_test_functions(newline.join(lines))
    assert parsed.imports == ("import os",)
    assert parsed.helpers == ("X = [\n    1,\n]",)
    assert parsed.tests == (("test_a", "def test_a():\n    assert os\n    assert X"),)


def test_rename_definition_normalises_line_endings() -> None:
    renamed = rename_definition("def test_a():\r\n    pass\r\n", "test_a", "test_b")
    assert renamed == "def test_b():\n    pass\n"


def test_empty_source_and_docstring_only() -> None:
    assert split_test_functions("") == ParsedTestModule()
    assert split_test_functions('"""Only docs."""\n') == ParsedTestModule()


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("def test_a(:\n    pass\n", id="syntax-error"),
        pytest.param("def test_a():\n  x = 1\n y = 2\n", id="bad-indentation"),
        pytest.param("x = 1\x00\n", id="null-byte"),
    ],
)
def test_invalid_source_raises_extraction_error(source: str) -> None:
    with pytest.raises(ExtractionError):
        split_test_functions(source)


# --------------------------------------------------------------------------- free-form wrapping


def test_make_free_form_tests_attaches_only_needed_helpers() -> None:
    tests = make_free_form_tests("add", MODULE)
    assert [t.name for t in tests] == [
        "test_add",
        "test_async",
        "test_uses_fixture",
        "test_uses_helper",
        "TestDiv",
    ]
    assert {t.function for t in tests} == {"add"}
    assert {t.origin for t in tests} == {CaseOrigin.LLM_FREE}
    by_name = {t.name: t.source for t in tests}
    for source in by_name.values():
        ast.parse(source)
        assert source.startswith("import os\nfrom calc import add, div\nimport pytest\n")
    assert "def acc()" not in by_name["test_add"]
    assert "def acc()" in by_name["test_uses_fixture"]
    assert "LIMIT = 10" in by_name["test_uses_helper"]  # needed through _helper
    assert "def _helper" in by_name["test_uses_helper"]
    assert "class Plain" not in "".join(by_name.values())


def test_make_free_form_tests_keeps_autouse_and_pytestmark() -> None:
    source = textwrap.dedent(
        """\
        import pytest

        pytestmark = pytest.mark.slow

        @pytest.fixture(autouse=True)
        def reset():
            yield

        @pytest.fixture
        def unused():
            return 1

        def test_a():
            assert True
        """
    )
    (test,) = make_free_form_tests("f", source)
    assert "pytestmark" in test.source
    assert "def reset" in test.source
    assert "def unused" not in test.source


def test_make_free_form_tests_finds_usefixtures_by_string() -> None:
    source = textwrap.dedent(
        """\
        import pytest

        @pytest.fixture
        def db():
            return {}

        @pytest.mark.usefixtures("db")
        def test_a():
            assert True
        """
    )
    (test,) = make_free_form_tests("f", source)
    assert "def db" in test.source


def test_make_free_form_tests_without_imports_or_tests() -> None:
    (test,) = make_free_form_tests("f", "def test_a():\n    assert 1\n")
    assert test.source == "def test_a():\n    assert 1\n"
    assert make_free_form_tests("f", "X = 1\n") == ()


def test_make_free_form_tests_propagates_extraction_error() -> None:
    with pytest.raises(ExtractionError):
        make_free_form_tests("f", "def (:")


# --------------------------------------------------------------------------- rename


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("def test_a():\n    pass", "def test_a_2():\n    pass", id="def"),
        pytest.param(
            "async  def test_a():\n    pass", "async  def test_a_2():\n    pass", id="async"
        ),
        pytest.param(
            "@d(test_a)\ndef test_a(x=test_a):\n    return test_a",
            "@d(test_a)\ndef test_a_2(x=test_a):\n    return test_a",
            id="decorated-only-the-definition",
        ),
        pytest.param("class test_a:\n    pass", "class test_a_2:\n    pass", id="class"),
        pytest.param("def test_other():\n    pass", "def test_other():\n    pass", id="no-match"),
    ],
)
def test_rename_definition(source: str, expected: str) -> None:
    assert rename_definition(source, "test_a", "test_a_2") == expected
