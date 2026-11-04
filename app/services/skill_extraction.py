"""Deterministic technology vocabulary shared by job extraction and profile matching."""

import re
from html.parser import HTMLParser

VERSION = "tech-skills-v1"
CATALOG = {
    "Python": ("python",),
    "Java": ("java",),
    "JavaScript": ("javascript", "java script", "js"),
    "TypeScript": ("typescript", "type script"),
    "Go": ("golang", "go"),
    "C++": ("c++",),
    "C#": ("c#", "c sharp", "csharp"),
    "PHP": ("php",),
    "Ruby": ("ruby",),
    "Rust": ("rust",),
    "Kotlin": ("kotlin",),
    "Swift": ("swift",),
    "Dart": ("dart",),
    "SQL": ("sql",),
    "PostgreSQL": ("postgresql", "postgres"),
    "MySQL": ("mysql", "my sql"),
    "SQL Server": ("sql server", "mssql", "microsoft sql server"),
    "MongoDB": ("mongodb", "mongo db"),
    "Redis": ("redis",),
    "Elasticsearch": ("elasticsearch", "elastic search"),
    "React": ("react", "reactjs", "react.js"),
    "Next.js": ("next.js", "nextjs", "next js"),
    "Vue.js": ("vue", "vue.js", "vuejs"),
    "Angular": ("angular", "angularjs"),
    "Node.js": ("node.js", "nodejs", "node js"),
    "Express.js": ("express.js", "expressjs"),
    "Spring Boot": ("spring boot", "springboot"),
    "Django": ("django",),
    "Flask": ("flask",),
    "FastAPI": ("fastapi", "fast api"),
    "Laravel": ("laravel",),
    ".NET": (".net", "dotnet", "asp.net", "asp.net core"),
    "Flutter": ("flutter",),
    "HTML": ("html", "html5"),
    "CSS": ("css", "css3"),
    "Tailwind CSS": ("tailwind", "tailwind css", "tailwindcss"),
    "Docker": ("docker",),
    "Kubernetes": ("kubernetes", "k8s"),
    "Terraform": ("terraform",),
    "Ansible": ("ansible",),
    "AWS": ("aws", "amazon web services"),
    "Google Cloud": ("gcp", "google cloud", "google cloud platform"),
    "Azure": ("azure", "microsoft azure"),
    "Linux": ("linux",),
    "Git": ("git",),
    "GitHub Actions": ("github actions",),
    "GitLab CI": ("gitlab ci", "gitlab ci/cd"),
    "CI/CD": ("ci/cd", "ci cd", "continuous integration", "continuous delivery"),
    "Jenkins": ("jenkins",),
    "Kafka": ("kafka", "apache kafka"),
    "RabbitMQ": ("rabbitmq", "rabbit mq"),
    "GraphQL": ("graphql",),
    "REST API": ("rest api", "restful api", "rest apis", "restful apis"),
    "gRPC": ("grpc",),
    "PyTorch": ("pytorch", "py torch"),
    "TensorFlow": ("tensorflow", "tensor flow"),
    "scikit-learn": ("scikit-learn", "sklearn", "scikit learn"),
    "Pandas": ("pandas",),
    "NumPy": ("numpy",),
    "Apache Spark": ("apache spark", "pyspark"),
    "Airflow": ("airflow", "apache airflow"),
    "Selenium": ("selenium",),
    "Playwright": ("playwright",),
    "Cypress": ("cypress",),
    "Jest": ("jest",),
    "pytest": ("pytest",),
}
ALIASES = {
    alias.casefold(): name for name, aliases in CATALOG.items() for alias in aliases
}


def canonical(name):
    name = name.strip()
    return ALIASES.get(name.casefold(), name)


def pattern_for(name):
    if name == "Go":
        # Avoid ordinary prose such as "go to the office".
        expression = r"golang|(?-i:Go|GO)(?!\s+(?:to|ahead|home|beyond|through)\b)"
    else:
        expression = "|".join(
            re.escape(alias)
            for alias in sorted(CATALOG.get(name, (name,)), key=len, reverse=True)
        )
    return re.compile(r"(?<![\w.+#])(?:" + expression + r")(?![\w+#])", re.IGNORECASE)


PATTERNS = {name: pattern_for(name) for name in CATALOG}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "li", "br", "div", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "li", "div"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    parser = _Text()
    parser.feed(value or "")
    return "".join(parser.parts)


def effective_skills(job, structured):
    """Explicit skills take precedence; otherwise derive skills without editing source records."""
    if structured:
        names = {
            canonical(name).casefold(): (canonical(name), name.strip())
            for name in structured
            if name.strip()
        }
        return [
            {"skill": name, "field": "skills", "excerpt": excerpt}
            for name, excerpt in sorted(
                names.values(), key=lambda item: item[0].casefold()
            )
        ]
    found = {}
    for field in (
        "requirements",
        "description",
        "responsibilities",
        "experience_requirements",
        "preferred_qualifications",
    ):
        text = plain_text(getattr(job, field, None))
        candidates = []
        for name, pattern in PATTERNS.items():
            candidates.extend(
                (match.start(), match.end(), name) for match in pattern.finditer(text)
            )
        occupied = []
        for start, end, name in sorted(
            candidates, key=lambda item: (item[0], -item[1])
        ):
            if any(start < right and end > left for left, right in occupied):
                continue
            occupied.append((start, end))
            if name not in found:
                excerpt = " ".join(text[max(0, start - 70) : end + 100].split())
                found[name] = {"skill": name, "field": field, "excerpt": excerpt}
    return [found[name] for name in sorted(found, key=str.casefold)]
