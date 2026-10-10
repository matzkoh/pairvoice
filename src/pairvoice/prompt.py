"""要約のプロンプト。共通の部分（prompt.txt）に、声ごとの口調（tone.txt）を足して組む。

口調（口調の指示と入出力の例）だけを声ごとに差し替え、削り方や言葉選びは共通にする。
口調の例が共通の側に残っていると、声の口調を差し替えても例に引き戻される。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .history import VersionedText
from .profiles import PROFILES_DIRNAME, TONE_FILENAME, Profile, ProfileStore

PROMPT_FILENAME = "prompt.txt"


class SummaryPrompt:
    """要約に渡すシステムプロンプトを、声ごとに組む。"""

    def __init__(self, data_root: Path) -> None:
        self.profiles = ProfileStore(data_root / PROFILES_DIRNAME)
        self.common = VersionedText(data_root / PROMPT_FILENAME, "prompt")
        # 口調を持たない声が使う、既定の口調
        self.default_tone = VersionedText(data_root / TONE_FILENAME, "tone")

    def _tone_source(self, profile: Profile | None) -> VersionedText:
        """その声で使う口調。声の口調が空なら既定の口調。"""
        # 声の口調は Profile を引いたときに読んである
        if profile is not None and profile.tone.strip():
            return self.profiles.tone(profile.id)
        return self.default_tone

    def _tone_text(self, profile: Profile | None) -> str:
        if profile is not None and profile.tone.strip():
            return profile.tone.strip()
        return self.default_tone.read().strip()

    def compose(
        self, profile: Profile | None, common: str | None = None, tone: str | None = None
    ) -> str:
        """共通の部分に、声の口調を足す。common・tone を渡せば、それぞれその候補で組む。"""
        base = self.common.read() if common is None else common
        tone = self._tone_text(profile) if tone is None else tone.strip()
        if not tone:
            return base
        return f"{base.rstrip()}\n\n{tone}\n"

    def compose_for(
        self, voice: str | None, common: str | None = None, tone: str | None = None
    ) -> tuple[str, Profile | None]:
        """声の名前か ID（省けば使用中の声）で組む。無い声は ProfileNotFound。"""
        profile = self.profiles.resolve(voice)
        return self.compose(profile, common, tone), profile

    def current_since(self, profile: Profile | None) -> datetime | None:
        """その声で組むプロンプトが、いまの中身で動き出した時刻（共通と口調の遅い方）。

        どちらかがいつから動いているか分からなければ None（境界を引かない）。
        口調が空のまま一度も書いていないなら、共通の部分だけで決まる。
        """
        common = self.common.current_since()
        source = self._tone_source(profile)
        if not self._tone_text(profile) and not source.versions():
            return common
        since = source.current_since()
        if common is None or since is None:
            return None
        return max(common, since)
