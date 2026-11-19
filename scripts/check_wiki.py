#!/usr/bin/env python3
"""Проверка исходников вики (wiki/*.md) перед публикацией.

Ссылки на страницы ведут на существующие файлы; у каждой страницы есть «*Для кого:*», «## Что дальше» и пункт в _Sidebar.md;
нет ссылок [[…]] и служебных пометок для разработчиков. Выход 1 при ошибках.

Использование: scripts/check_wiki.py [--self-test]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WIKI = Path(__file__).resolve().parent.parent / "wiki"
SERVICE = {"Home.md", "_Sidebar.md", "_Footer.md"}
LINK = re.compile(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")
IMAGE = re.compile(r"!\[[^\]]*\]\((images/[^)\s]+)\)")
# служебные пометки и слова, которые читателю не нужны
FORBIDDEN = re.compile(r"\[\[\S|Относится к версии|уровень проверки|\bстенд\w*|✔", re.IGNORECASE)


def check_page(name: str, text: str, pages: set[str], images: set[str]) -> list[str]:
    errors = []
    for m in LINK.finditer(text):
        target = m.group(1)
        if "/" not in target and target not in pages:
            errors.append(f"ссылка на несуществующую страницу {target}")
    errors += [f"нет изображения {m.group(1)}" for m in IMAGE.finditer(text) if m.group(1) not in images]
    errors += [f"служебная пометка «{m.group(0)}»" for m in FORBIDDEN.finditer(text)]
    if name not in SERVICE:
        if "*Для кого:" not in text:
            errors.append("нет строки «*Для кого: …*»")
        if "\n## Что дальше" not in text:
            errors.append("нет блока «## Что дальше»")
    return errors


def main() -> int:
    pages = {p.name for p in WIKI.glob("*.md")}
    images = {f"images/{p.name}" for p in (WIKI / "images").glob("*")}
    sidebar = (WIKI / "_Sidebar.md").read_text(encoding="utf-8")
    in_sidebar = set(LINK.findall(sidebar))
    report = []
    for name in sorted(pages):
        text = (WIKI / name).read_text(encoding="utf-8")
        report += [f"{name}: {e}" for e in check_page(name, text, pages, images)]
        if name not in SERVICE and name not in in_sidebar:
            report.append(f"{name}: нет в _Sidebar.md")
    print("\n".join(report) or f"вики: {len(pages)} страниц, ошибок нет")
    return 1 if report else 0


def self_test() -> None:
    pages = {"A.md", "B.md"}
    good = "# A\n\n*Для кого: все.*\n\nСм. [B](B.md#раздел) и [файл](https://example.org/x.md).\n\n## Что дальше\n- [B](B.md)\n"
    assert check_page("A.md", good, pages, set()) == [], check_page("A.md", good, pages, set())
    bad = "# A\n\nСм. [C](C.md), [[B]], проверено на стенде, ![x](images/no.png)\n"
    errors = " ".join(check_page("A.md", bad, pages, set()))
    for expect in ("C.md", "[[", "стенде", "images/no.png", "Для кого", "Что дальше"):
        assert expect in errors, (expect, errors)
    print("self-test: ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
        sys.exit(0)
    sys.exit(main())
