"""The documents a new owner reads say what the code does: links lead
somewhere, screens are named as the bot names them, and `cp .env.example
.env` changes nothing but the token."""

import ast
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted([ROOT / "README.md", ROOT / "README.ru.md", *ROOT.glob("docs/*.md")])
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

# Screens and buttons the bot does not have and claims the code does not
# make — each was in the docs once and sent people looking for nothing.
# A name that the bot's own texts do use is fine.
STALE = {
    "🩺": "no «Диагностика» screen — «⚙️ Настройки → 🔌 Подключения»",
    "diagnostics": "no diagnostics screen — «⚙️ Настройки → 🔌 Подключения»",
    "Сайт детально": "sites live in «🌐 Сайты»",
    "Проверить всё сейчас": "the main screen has no such button",
    "Сводки и тихие часы": "it is «🔔 Когда писать»",
    "Видел, не напоминать 2 ч": "no such button",
    "секрет-из-диагностики": "the bot's snippet already holds the secret",
    "One GraphQL query": "two paginated GraphQL queries",
    "Один GraphQL-запрос": "два постраничных GraphQL-запроса",
    "📈 Доступность": "no such screen",
    "пшеничн": "a <pre> chart has no colour",
    "one site in detail": "old main screen",
    "один сайт подробно": "old main screen",
    "No external monitoring dependencies": "check-host, microlink and others are used",
    "https://your-server:8080": "port 8080 is plain HTTP",
    "export/import": "there is no export",
}


def _slug(heading: str) -> str:
    """GitHub's anchor: lower case, letters/digits/space/-/_ kept, spaces → -."""
    kept = (ch for ch in heading.strip().lower()
            if ch in " -_" or unicodedata.category(ch)[0] in "LN")
    return "".join(kept).replace(" ", "-")


def _anchors(md: Path) -> set[str]:
    found, seen, in_code = set(), {}, False
    for line in md.read_text("utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        elif not in_code and (m := re.match(r"^#{1,6}\s+(.*)$", line)):
            s = _slug(re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", r"\1", m.group(1)))
            found.add(s if s not in seen else f"{s}-{seen[s]}")
            seen[s] = seen.get(s, 0) + 1
    return found


def test_relative_links_in_the_docs_lead_somewhere():
    broken = []
    for md in DOCS:
        in_code = False
        for no, line in enumerate(md.read_text("utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_code = not in_code
                continue
            for target in [] if in_code else LINK.findall(line):
                if re.match(r"^[a-z]+://|^mailto:", target):
                    continue
                path, _, frag = target.partition("#")
                dest = (md.parent / path).resolve() if path else md
                if not dest.exists():
                    broken.append(f"{md.relative_to(ROOT)}:{no}: {target} — no such file")
                elif frag and dest.suffix == ".md" and frag not in _anchors(dest):
                    broken.append(f"{md.relative_to(ROOT)}:{no}: {target} — no such heading")
    assert broken == [], "\n".join(broken)


def _bot_texts() -> str:
    """Every string the code can show (docstrings are not shown, so not counted)."""
    texts = []
    for py in ROOT.glob("**/*.py"):
        if {"tests", "audit", ".venv", "venv"} & set(py.relative_to(ROOT).parts):
            continue
        tree = ast.parse(py.read_text("utf-8"))
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        texts += [n.value for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]
    return "\n".join(texts)


def test_docs_do_not_send_people_to_screens_that_do_not_exist():
    shown = _bot_texts()
    found = []
    for path in [*DOCS, ROOT / ".env.example", ROOT / "Dockerfile"]:
        for no, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            found += [f"{path.relative_to(ROOT)}:{no}: «{word}» — {why}"
                      for word, why in STALE.items() if word in line and word not in shown]
    assert found == [], "\n".join(found)


def test_docs_hold_no_token_shaped_strings():
    """A realistic example token trips every secret scanner of every fork."""
    shaped = re.compile(r"[0-9]{8,10}:[A-Za-z0-9_-]{35}")
    hits = [f"{p.relative_to(ROOT)}:{n}" for p in [*DOCS, ROOT / ".env.example"]
            for n, line in enumerate(p.read_text("utf-8").splitlines(), 1) if shaped.search(line)]
    assert hits == []


_CONFIG_DUMP = (
    "import dataclasses, json, sys\n"
    "import dotenv\n"
    "dotenv.load_dotenv = lambda *a, **k: False   # never read a real .env\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import config\n"
    "print(json.dumps({f.name: getattr(config.config, f.name) for f in dataclasses.fields(config.Config)},"
    " default=str))\n")


def _config_with(env: dict[str, str]) -> dict:
    """The config a fresh process builds from exactly these variables (the
    test process itself runs with tests/conftest.py's values)."""
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "HOME", "LANG")}
    base |= {"PYTHONDONTWRITEBYTECODE": "1", "TELEGRAM_BOT_TOKEN": "0:fake"}
    out = subprocess.run([sys.executable, "-c", _CONFIG_DUMP, str(ROOT)], env={**base, **env},
                         capture_output=True, text=True, check=True, timeout=60)
    return json.loads(out.stdout)


def test_env_example_changes_nothing_but_the_token():
    """`cp .env.example .env` must give the same bot as no .env at all
    (Railway, the one-line .env of the README): the template shows the
    defaults, it does not choose new ones."""
    example = {k: v or "" for k, v in dotenv_values(ROOT / ".env.example").items()}
    example["TELEGRAM_BOT_TOKEN"] = "0:fake"
    bare, templated = _config_with({}), _config_with(example)
    differ = {k: (bare[k], templated[k]) for k in bare if bare[k] != templated[k]}
    assert differ == {}, differ                       # field: (no .env, cp .env.example .env)
    code = "\n".join(p.read_text("utf-8") for p in ROOT.glob("**/*.py")
                     if not {"tests", "audit", ".venv", "venv"} & set(p.relative_to(ROOT).parts))
    unread = [k for k in example if f'"{k}"' not in code]
    assert unread == [], unread                       # a variable nothing reads
