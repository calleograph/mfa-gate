#!/usr/bin/env bash
# Публикация wiki/*.md в GitHub Wiki репозитория. Wiki должна быть инициализирована: один раз создайте любую страницу
# через веб-интерфейс (вкладка Wiki → Create the first page), затем запускайте этот скрипт.
#   scripts/publish-wiki.sh [owner/repo]
set -euo pipefail
python3 "$(dirname "${BASH_SOURCE[0]}")/check_wiki.py"
REPO="${1:-calleograph/mfa-gate}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone -q "https://github.com/$REPO.wiki.git" "$TMP/wiki" || { echo "Wiki не инициализирована: создайте первую страницу через веб-интерфейс"; exit 1; }
for f in "$ROOT"/wiki/*.md; do
  # в GitHub Wiki ссылки на страницы — без расширения .md
  sed -E 's/\]\(([^)/#]+)\.md(#[^)]*)?\)/](\1\2)/g' "$f" > "$TMP/wiki/$(basename "$f")"
done
# изображения — вместе со страницами; служебный wiki/_meta не публикуется
[[ -d "$ROOT/wiki/images" ]] && mkdir -p "$TMP/wiki/images" && cp "$ROOT"/wiki/images/* "$TMP/wiki/images/"
cd "$TMP/wiki"
git add -A
git diff --cached --quiet && { echo "изменений нет"; exit 0; }
git commit -q -m "Документация MFA Gate $(cat "$ROOT/VERSION")"
git push -q
echo "опубликовано: https://github.com/$REPO/wiki"
