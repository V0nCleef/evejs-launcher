"""Character ordering and batch launch phrases in every supported launcher language."""
SOURCE_PHRASES = (
    'Drag to reorder characters',
    'Character order not saved',
    'Could not save the character order. Your previous order was kept. Check the launcher logs and try again.',
)
_VALUES = {
    'nl': ('Sleep om personages te herschikken', 'Personagevolgorde niet opgeslagen',
           'De personagevolgorde kon niet worden opgeslagen. Je vorige volgorde is behouden. Controleer de launcherlogboeken en probeer het opnieuw.'),
    'de': ('Ziehen, um Charaktere neu anzuordnen', 'Charakterreihenfolge nicht gespeichert',
           'Die Charakterreihenfolge konnte nicht gespeichert werden. Die vorherige Reihenfolge wurde beibehalten. Prüfe die Launcher-Protokolle und versuche es erneut.'),
    'fr': ('Glissez pour réorganiser les personnages', 'Ordre des personnages non enregistré',
           'Impossible d’enregistrer l’ordre des personnages. L’ordre précédent a été conservé. Consultez les journaux du lanceur et réessayez.'),
    'ru': ('Перетащите, чтобы изменить порядок персонажей', 'Порядок персонажей не сохранён',
           'Не удалось сохранить порядок персонажей. Предыдущий порядок сохранён. Проверьте журналы лаунчера и повторите попытку.'),
    'zh_CN': ('拖动以调整角色顺序', '角色顺序未保存',
              '无法保存角色顺序。已保留原来的顺序。请查看启动器日志后重试。'),
    'ja': ('ドラッグしてキャラクターを並べ替え', 'キャラクターの並び順を保存できませんでした',
           'キャラクターの並び順を保存できませんでした。以前の並び順を保持しています。ランチャーのログを確認して、もう一度お試しください。'),
    'ko': ('드래그하여 캐릭터 순서 변경', '캐릭터 순서가 저장되지 않음',
           '캐릭터 순서를 저장하지 못했습니다. 이전 순서가 유지됩니다. 런처 로그를 확인한 후 다시 시도하세요.'),
}
_BATCH_SOURCE = (
    'This group has no visible, non-banned characters.',
    'This group is empty.',
    'No visible characters. Check hidden characters on the Characters page.',
    'No characters are loaded. Open the Characters page.',
)
_BATCH_VALUES = {
    'nl': ('Deze groep heeft geen zichtbare, niet-verbannen personages.', 'Deze groep is leeg.',
           'Geen zichtbare personages. Controleer verborgen personages op de personagepagina.',
           'Er zijn geen personages geladen. Open de personagepagina.'),
    'de': ('Diese Gruppe enthält keine sichtbaren, nicht gesperrten Charaktere.', 'Diese Gruppe ist leer.',
           'Keine sichtbaren Charaktere. Prüfe ausgeblendete Charaktere auf der Charakterseite.',
           'Es sind keine Charaktere geladen. Öffne die Charakterseite.'),
    'fr': ('Ce groupe ne contient aucun personnage visible et non banni.', 'Ce groupe est vide.',
           'Aucun personnage visible. Vérifiez les personnages masqués sur la page Personnages.',
           'Aucun personnage chargé. Ouvrez la page Personnages.'),
    'ru': ('В этой группе нет видимых незаблокированных персонажей.', 'Эта группа пуста.',
           'Нет видимых персонажей. Проверьте скрытых персонажей на странице персонажей.',
           'Персонажи не загружены. Откройте страницу персонажей.'),
    'zh_CN': ('此分组没有可见且未被封禁的角色。', '此分组为空。',
              '没有可见角色。请在角色页面查看隐藏角色。', '未加载任何角色。请打开角色页面。'),
    'ja': ('このグループには表示可能な未停止のキャラクターがいません。', 'このグループは空です。',
           '表示可能なキャラクターがいません。キャラクターページで非表示のキャラクターを確認してください。',
           'キャラクターが読み込まれていません。キャラクターページを開いてください。'),
    'ko': ('이 그룹에 표시 가능한 미차단 캐릭터가 없습니다.', '이 그룹은 비어 있습니다.',
           '표시할 캐릭터가 없습니다. 캐릭터 페이지에서 숨긴 캐릭터를 확인하세요.',
           '불러온 캐릭터가 없습니다. 캐릭터 페이지를 여세요.'),
}
SOURCE_PHRASES += _BATCH_SOURCE
UI_PHRASES_BY_LANGUAGE = {
    language: dict(zip(SOURCE_PHRASES, values + _BATCH_VALUES[language], strict=True))
    for language, values in _VALUES.items()
}
