#!/usr/bin/env bash
# タグ（例: v0.21.0）のリリースノートを、前のタグからの feat / fix のコミットの件名で組む。
# release.yml が GitHub Releases に載せる。メニューバーの「変更点を見る」はその一覧を開く
set -euo pipefail

tag=$1
range=$tag
if previous=$(git describe --tags --abbrev=0 "$tag^" 2>/dev/null); then
  range=$previous..$tag
fi

section() {
  local items
  items=$(git log --format=%s "$range" | sed -nE "s/^$1(\([^)]*\))?: /- /p")
  if [ -n "$items" ]; then
    printf '## %s\n\n%s\n\n' "$2" "$items"
  fi
}

section feat 新しいこと
section fix 直したこと
