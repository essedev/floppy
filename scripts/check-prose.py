#!/usr/bin/env python3
"""
Finds Italian prose where a public repository promises English, and checks that the
bilingual README pair stays aligned.

Usage: check-prose.py [ROOT] [--summary]

What is scanned, among the files git knows about (tracked plus untracked, not ignored):
  - prose files (.md, .mdx, .txt, .rst), outside code fences and inline code;
  - comments in code files, and the names of tests (`it('...')`, `test(...)`,
    `describe(...)`); string literals are product, not prose, and are never scanned.

A line counts as Italian when it has a grave-accented letter or an Italian-only accented
word (perché, né), or at least two words that are Italian and not English. Short English
lines with a stray "la" or "con" do not trip it.

Paths that are Italian on purpose (localized content, the Italian README, the archive)
are allowed by default; a repository adds its own in `.prose-allow`, one glob per line.
A single line opts out with the marker `prose-allow` anywhere in it.

README pair: when README.md exists, README.it.md must exist too, start with the language
switch (`**English** · [Italiano](README.it.md)`, mirrored in Italian), and have the same sequence of heading levels and the same set of link targets.

Exit code 1 when anything is found, so it can sit in a lint script. Standard library only.
Canonical copy: the public-repo skill in claude-setup; repositories vendor it in
scripts/ and get it refreshed from there.
"""

import fnmatch
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

VERSION = "1"

DEFAULT_ALLOW = [
    "README.it.md",
    "**/it.md",
    "**/it.json",
    "**/*.it.md",
    "**/*.it.json",
    "**/it/**",
    "**/locales/it*",
    "**/i18n/it*",
    "docs/archive/**",
    "docs/concepts/**",
    "**/archive/**",
    "CHANGELOG.md",
    ".prose-allow",
]

SKIP_SUFFIXES = {
    ".lock", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".svg", ".pdf",
    ".woff", ".woff2", ".ttf", ".otf", ".zip", ".gz", ".mp4", ".mov", ".webm", ".wasm",
}
SKIP_NAMES = {"pnpm-lock.yaml", "package-lock.json", "uv.lock", "Cargo.lock", "Package.resolved"}

PROSE = {".md", ".mdx", ".txt", ".rst"}
C_LIKE = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".swift", ".rs", ".go", ".java", ".kt",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".m", ".css", ".scss", ".zig", ".dart",
}
MARKUP = {".svelte", ".astro", ".vue", ".html"}
HASH = {".sh", ".bash", ".zsh", ".rb", ".toml", ".yaml", ".yml", ".nix"}
HASH_NAMES = {"Makefile", "Dockerfile", "Justfile", ".gitignore", ".env.example"}
DASH = {".sql", ".lua"}

# Words that are Italian and never English (or so rare in English prose that two of them
# on one line settle it). "come", "solo", "con", "la", "per", "non" are left out on purpose.
ITALIAN = set(
    """
    il gli lo della delle dello degli dei del dal dalla dalle dai nel nella nelle negli nei
    sul sulla sulle sui alla alle agli ai che è sono questo questa questi queste quello
    quella quando anche senza dove ogni già così quindi invece oppure ancora sempre mai
    qui viene vengono essere serve servono deve devono fatto cosa tutti tutto tutte una uno
    un altro altra altri prima dopo poi perché poiché però mentre finché se ma ed od
    più meno molto troppo stesso stessa può possono fa fanno va vanno resta restano
    nessun nessuna niente nulla qualcosa tra fra sopra sotto dentro fuori verso contro
    perciò cioè ossia infatti allora già ciò cui quale quali chi questa lui lei loro
    """.split()
)
# Also English words (or names, or acronyms): never counted.
ENGLISH_GUARD = {
    "a", "i", "e", "o", "in", "to", "is", "be", "do", "so", "me", "no", "un", "ai", "fa", "ma",
    "lo", "ed", "od", "serve", "dove", "verso", "prima", "chi", "poi", "tutti", "fatto",
}
ITALIAN -= ENGLISH_GUARD

GRAVE = re.compile(r"[àèìòùÀÈÌÒÙ]")
ACUTE_WORD = re.compile(r"\b(perché|poiché|affinché|finché|benché|né|sé|ché)\b", re.I)
WORD = re.compile(r"[A-Za-zÀ-ÿ']+")
TEST_NAME = re.compile(r"\b(?:it|test|describe|suite|context)(?:\.\w+)?\(\s*(['\"`])(.+?)\1")
STRINGS = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`")
SWITCH = {
    "README.md": re.compile(r"^\s*\*\*English\*\* · \[Italiano\]\(README\.it\.md\)"),
    "README.it.md": re.compile(r"^\s*\[English\]\(README\.md\) · \*\*Italiano\*\*"),
}
HEADING = re.compile(r"^(#{1,6})\s")
LINK = re.compile(r"\]\(([^)\s]+)")


def italian(text: str) -> bool:
    if "prose-allow" in text:
        return False
    if GRAVE.search(text) or ACUTE_WORD.search(text):
        return True
    hits = [w for w in WORD.findall(text.lower()) if w.strip("'") in ITALIAN]
    return len(hits) >= 2


def git_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def allowed(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]):
            return True
    return False


def prose_lines(text: str):
    fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        yield number, re.sub(r"`[^`]*`", "", line)


def c_like_comments(text: str, markup: bool):
    """Comments of C-like code; with markup, also <!-- --> blocks."""
    block = None  # closing token of the open block comment
    for number, line in enumerate(text.splitlines(), 1):
        rest = line
        found = []
        while rest:
            if block:
                end = rest.find(block)
                if end == -1:
                    found.append(rest)
                    rest = ""
                else:
                    found.append(rest[:end])
                    rest = rest[end + len(block):]
                    block = None
                continue
            bare = STRINGS.sub(lambda m: " " * len(m.group(0)), rest)
            starts = [(bare.find(tok), tok) for tok in ("//", "/*") + (("<!--",) if markup else ())]
            starts = [(i, tok) for i, tok in starts if i != -1]
            if not starts:
                break
            index, token = min(starts)
            if token == "//":
                if index > 0 and bare[index - 1] == ":":  # a URL in code, not a comment
                    rest = rest[index + 2:]
                    continue
                found.append(rest[index + 2:])
                break
            block = "*/" if token == "/*" else "-->"
            rest = rest[index + len(token):]
        for match in TEST_NAME.finditer(line):
            found.append(match.group(2))
        text_found = " ".join(found).strip()
        if text_found:
            yield number, text_found


def marker_comments(text: str, marker: str):
    for number, line in enumerate(text.splitlines(), 1):
        bare = STRINGS.sub(lambda m: " " * len(m.group(0)), line)
        index = bare.find(marker)
        if index != -1 and not bare[index:].startswith("#!"):
            yield number, line[index + len(marker):]


def python_comments(text: str):
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        yield from marker_comments(text, "#")
        return
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            yield tok.start[0], tok.string[1:]
        elif tok.type == tokenize.STRING and tok.string.lstrip("rbuRBU").startswith(('"""', "'''")):
            for offset, line in enumerate(tok.string.splitlines()):
                yield tok.start[0] + offset, line
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"\s*def (test_\w+)", line)
        if match:
            yield number, match.group(1).replace("_", " ")


def scan(path: Path, rel: str):
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return
    if re.search(r"@generated|generated by|do not edit", "\n".join(text.splitlines()[:3]), re.I):
        return
    if suffix in PROSE:
        lines = prose_lines(text)
    elif suffix == ".py":
        lines = python_comments(text)
    elif suffix in C_LIKE:
        lines = c_like_comments(text, markup=False)
    elif suffix in MARKUP:
        lines = c_like_comments(text, markup=True)
    elif suffix in HASH or path.name in HASH_NAMES:
        lines = marker_comments(text, "#")
    elif suffix in DASH:
        lines = marker_comments(text, "--")
    else:
        return
    for number, line in lines:
        if italian(line):
            yield rel, number, line.strip()


def readme_pair(root: Path):
    """Problems with the README.md / README.it.md pair."""
    en, it = root / "README.md", root / "README.it.md"
    if not en.exists():
        return []
    if not it.exists():
        return ["README.it.md is missing (public repositories keep a bilingual README)"]
    problems = []
    texts = {name: f.read_text(encoding="utf-8") for name, f in (("README.md", en), ("README.it.md", it))}
    for name, text in texts.items():
        if not any(SWITCH[name].match(line) for line in text.splitlines()[:30]):
            problems.append(f"{name}: no language switch in the first 30 lines")

    def shape(text):
        return [len(m.group(1)) for _, line in prose_lines(text) if (m := HEADING.match(line))]

    def links(text):
        # In-page anchors are language-specific (#installation, #installazione), and the
        # switch links each README to the other: neither has to match.
        return {u for u in LINK.findall(text) if not u.startswith("#") and "README" not in u}

    if shape(texts["README.md"]) != shape(texts["README.it.md"]):
        problems.append("README.md and README.it.md have different heading structures")
    missing = links(texts["README.md"]) ^ links(texts["README.it.md"])
    if missing:
        problems.append("links in only one README: " + ", ".join(sorted(missing)))
    return problems


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    summary = "--summary" in sys.argv
    root = Path(args[0] if args else ".").resolve()
    patterns = list(DEFAULT_ALLOW)
    allow_file = root / ".prose-allow"
    if allow_file.exists():
        patterns += [
            line.strip() for line in allow_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]

    findings = []
    for rel in git_files(root):
        path = root / rel
        if path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_NAMES or not path.is_file():
            continue
        if allowed(rel, patterns):
            continue
        findings.extend(scan(path, rel))
    problems = readme_pair(root)

    if summary:
        counts = {}
        for rel, _, _ in findings:
            counts[rel] = counts.get(rel, 0) + 1
        for rel, count in sorted(counts.items(), key=lambda item: -item[1]):
            print(f"{count:5}  {rel}")
    else:
        for rel, number, line in findings:
            print(f"{rel}:{number}: {line[:120]}")
    for problem in problems:
        print(f"README: {problem}")
    if findings or problems:
        print(f"\n{len(findings)} Italian lines, {len(problems)} README problems (check-prose v{VERSION})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
