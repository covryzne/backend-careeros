from types import SimpleNamespace

from app.services.jobs.skills import canonical, effective_skills, pattern_for


def extract(text, **fields):
    return effective_skills(SimpleNamespace(description=text, **fields), [])


def test_aliases_boundaries_and_punctuation_without_substring_matches():
    result = extract("JavaScript, Node.js, Postgres, Golang, C++, C#, .NET and k8s.")
    assert {item["skill"] for item in result} == {
        "JavaScript", "Node.js", "PostgreSQL", "Go", "C++", "C#", ".NET", "Kubernetes"
    }
    assert canonical("Postgres") == "PostgreSQL"
    assert pattern_for("PostgreSQL").search("Built Postgres services")
    assert not pattern_for("Java").search("JavaScript")
    assert not pattern_for("Go").search("go to the office")
    assert not pattern_for("Go").search("Go to the office")


def test_extraction_is_grounded_deduplicated_and_ignores_hidden_html():
    result = extract(
        "<script>Python</script><style>Java</style><p>Use Postgres &amp; Docker.</p>",
        requirements="Experience with PostgreSQL and Docker required.",
        title="Rust Engineer",
    )
    assert [item["skill"] for item in result] == ["Docker", "PostgreSQL"]
    assert all(item["field"] == "requirements" for item in result)
    assert all(item["skill"] in item["excerpt"] for item in result)
    assert extract("Nothing technical was specified.") == []
    assert [item["skill"] for item in extract("SQL Server and Tailwind CSS")] == [
        "SQL Server", "Tailwind CSS"
    ]


def test_explicit_skills_are_authoritative_and_aliases_are_not_double_counted():
    result = effective_skills(SimpleNamespace(description="Java and Docker"), [
        "Postgres", "PostgreSQL", "Custom Tool", "custom tool"
    ])
    assert {item["skill"].casefold() for item in result} == {"postgresql", "custom tool"}
    assert all(item["field"] == "skills" for item in result)
