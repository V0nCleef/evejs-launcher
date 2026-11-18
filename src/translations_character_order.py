"""Character ordering phrases in every supported launcher language."""
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
UI_PHRASES_BY_LANGUAGE = {language:dict(zip(SOURCE_PHRASES, values, strict=True))
                          for language,values in _VALUES.items()}
