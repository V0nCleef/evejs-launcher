"""Launcher-owned mod update framing; release notes remain author text."""
from __future__ import annotations

LANGUAGE_CODES = ("zh_CN", "ja", "ko", "fr", "de", "nl", "ru")
_PHRASES: dict[str, tuple[str, ...]] = {
    "Check mod updates": (
        "检查模组更新", "MODの更新を確認", "모드 업데이트 확인",
        "Rechercher les mises à jour des mods", "Nach Mod-Updates suchen",
        "Op modupdates controleren", "Проверить обновления модов",
    ),
    "Update available": (
        "有可用更新", "更新があります", "업데이트 사용 가능",
        "Mise à jour disponible", "Update verfügbar", "Update beschikbaar", "Доступно обновление",
    ),
    "Mod update": (
        "模组更新", "MODの更新", "모드 업데이트",
        "Mise à jour du mod", "Mod-Update", "Modupdate", "Обновление мода",
    ),
    "Update mod": (
        "更新模组", "MODを更新", "모드 업데이트하기",
        "Mettre à jour le mod", "Mod aktualisieren", "Mod bijwerken", "Обновить мод",
    ),
    "Release notes": (
        "更新说明", "リリースノート", "릴리스 노트",
        "Notes de version", "Versionshinweise", "Versieopmerkingen", "Примечания к выпуску",
    ),
    "No release notes were provided.": (
        "未提供更新说明。", "リリースノートはありません。", "릴리스 노트가 제공되지 않았습니다.",
        "Aucune note de version fournie.", "Es wurden keine Versionshinweise angegeben.",
        "Er zijn geen versieopmerkingen opgegeven.", "Примечания к выпуску не предоставлены.",
    ),
    "View GitHub release": (
        "查看 GitHub 发布", "GitHubのリリースを表示", "GitHub 릴리스 보기",
        "Voir la version sur GitHub", "Release auf GitHub ansehen",
        "Release op GitHub bekijken", "Открыть выпуск на GitHub",
    ),
    "No mod updates are available.": (
        "没有可用的模组更新。", "MODの更新はありません。", "사용 가능한 모드 업데이트가 없습니다.",
        "Aucune mise à jour de mod disponible.", "Es sind keine Mod-Updates verfügbar.",
        "Er zijn geen modupdates beschikbaar.", "Обновления модов не найдены.",
    ),
    "Mod updates could not be checked.": (
        "无法检查模组更新。", "MODの更新を確認できませんでした。", "모드 업데이트를 확인하지 못했습니다.",
        "Impossible de vérifier les mises à jour des mods.", "Mod-Updates konnten nicht geprüft werden.",
        "Controleren op modupdates is mislukt.", "Не удалось проверить обновления модов.",
    ),
    "Update complete.": (
        "更新完成。", "更新が完了しました。", "업데이트가 완료되었습니다.",
        "Mise à jour terminée.", "Update abgeschlossen.", "Update voltooid.", "Обновление завершено.",
    ),
    "Recover mod update": (
        "恢复模组更新", "MODの更新を復旧", "모드 업데이트 복구",
        "Récupérer la mise à jour du mod", "Mod-Update wiederherstellen",
        "Modupdate herstellen", "Восстановить обновление мода",
    ),
    "Installed version": (
        "已安装版本", "インストール済みバージョン", "설치된 버전",
        "Version installée", "Installierte Version", "Geïnstalleerde versie", "Установленная версия",
    ),
    "Available version": (
        "可用版本", "利用可能なバージョン", "사용 가능한 버전",
        "Version disponible", "Verfügbare Version", "Beschikbare versie", "Доступная версия",
    ),
    "Mod updates": (
        "模组更新", "MODの更新", "모드 업데이트",
        "Mises à jour des mods", "Mod-Updates", "Modupdates", "Обновления модов",
    ),
}

_PHRASES.update({'Checking update': ('检查更新', '更新を確認中', '업데이트 확인 중', 'Vérification de la mise à jour', 'Update wird geprüft', 'Update controleren', 'Проверка обновления'), 'Downloading mod update': ('下载更新', '更新をダウンロード中', '업데이트 다운로드 중', 'Téléchargement de la mise à jour', 'Update wird heruntergeladen', 'Update downloaden', 'Загрузка обновления'), 'Checking package': ('检查安装包', 'パッケージを確認中', '패키지 확인 중', 'Vérification du paquet', 'Paket wird geprüft', 'Pakket controleren', 'Проверка пакета'), 'Backing up mod': ('备份模组', 'MODをバックアップ中', '모드 백업 중', 'Sauvegarde du mod', 'Mod wird gesichert', 'Mod back-uppen', 'Резервное копирование мода'), 'Installing mod update': ('安装更新', '更新をインストール中', '업데이트 설치 중', 'Installation de la mise à jour', 'Update wird installiert', 'Update installeren', 'Установка обновления'), 'Verifying installation': ('验证安装', 'インストールを検証中', '설치 검증 중', 'Vérification de l’installation', 'Installation wird überprüft', 'Installatie verifiëren', 'Проверка установки'), 'Restoring previous version': ('恢复上一版本', '以前のバージョンを復元中', '이전 버전 복원 중', 'Restauration de la version précédente', 'Vorherige Version wird wiederhergestellt', 'Vorige versie herstellen', 'Восстановление предыдущей версии'), 'Update failed': ('更新失败', '更新に失敗しました', '업데이트 실패', 'Échec de la mise à jour', 'Update fehlgeschlagen', 'Update mislukt', 'Ошибка обновления')})

SOURCE_PHRASES = tuple(_PHRASES)
UI_PHRASES_BY_LANGUAGE = {
    language: {source: values[index] for source, values in _PHRASES.items()}
    for index, language in enumerate(LANGUAGE_CODES)
}
