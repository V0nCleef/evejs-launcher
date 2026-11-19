"""Optional sound effects in every supported launcher language."""
SOURCE_PHRASES = (
    'Sound effects',
    'Sound effects volume',
    'Optional mod arrival and removal sounds from your installed EVE client.',
)
_VALUES = {
    'nl': ('Geluidseffecten', 'Volume geluidseffecten',
           'Optionele geluiden bij het toevoegen en verwijderen van mods uit je geïnstalleerde EVE-client.'),
    'de': ('Soundeffekte', 'Lautstärke der Soundeffekte',
           'Optionale Sounds beim Hinzufügen und Entfernen von Mods aus deinem installierten EVE-Client.'),
    'fr': ('Effets sonores', 'Volume des effets sonores',
           'Sons facultatifs d’ajout et de suppression des mods provenant de votre client EVE installé.'),
    'ru': ('Звуковые эффекты', 'Громкость звуковых эффектов',
           'Необязательные звуки добавления и удаления модов из установленного клиента EVE.'),
    'zh_CN': ('音效', '音效音量', '使用已安装 EVE 客户端的音频，为模组添加和移除播放可选音效。'),
    'ja': ('効果音', '効果音の音量', 'インストール済みのEVEクライアントの音声で、MODの追加・削除時に効果音を再生します。'),
    'ko': ('효과음', '효과음 음량', '설치된 EVE 클라이언트의 오디오로 모드 추가 및 제거 시 선택적 효과음을 재생합니다.'),
}
UI_PHRASES_BY_LANGUAGE = {language: dict(zip(SOURCE_PHRASES, values, strict=True))
                          for language, values in _VALUES.items()}
