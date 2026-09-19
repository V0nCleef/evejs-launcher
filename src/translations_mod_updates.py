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

_PHRASES.update({
    "What does this mean?": (
        "这是什么意思？", "これはどういう意味ですか？", "무슨 뜻인가요?",
        "Qu’est-ce que cela signifie ?", "Was bedeutet das?", "Wat betekent dit?", "Что это значит?",
    ),
    "Open the explanation and migration steps in How to make a mod on GitHub.": (
        "在 GitHub 的 How to make a mod 指南中打开说明和迁移步骤。",
        "GitHub の How to make a mod ガイドで説明と移行手順を開きます。",
        "GitHub의 How to make a mod 가이드에서 설명과 이전 절차를 엽니다.",
        "Ouvrir l’explication et les étapes de migration dans How to make a mod sur GitHub.",
        "Erklärung und Migrationsschritte in How to make a mod auf GitHub öffnen.",
        "Open de uitleg en overstapstappen in How to make a mod op GitHub.",
        "Открыть объяснение и шаги перехода в руководстве How to make a mod на GitHub.",
    ),
    "Legacy client script patch — still supported": (
        "旧式客户端脚本补丁 — 仍受支持", "従来のクライアントスクリプト変更 — 引き続き対応", "기존 클라이언트 스크립트 패치 — 계속 지원됨",
        "Modification classique des scripts clients — toujours prise en charge", "Älterer Client-Skriptpatch — weiterhin unterstützt",
        "Oudere clientscriptpatch — blijft ondersteund", "Прямое изменение скриптов клиента — по-прежнему поддерживается",
    ),
    "This mod last reported directly modifying client scripts for this client and backend. It remains supported, but authors should migrate to login-handshake delivery where possible.": (
        "此模组最近报告为此客户端和后端直接修改客户端脚本。该方式仍受支持，但建议作者在可行时迁移到登录握手交付方式。",
        "この MOD は、このクライアントとバックエンドでスクリプトを直接変更すると最後に報告しました。引き続き対応していますが、作者には可能な場合ログイン時のハンドシェイクによる配信への移行を推奨します。",
        "이 모드는 해당 클라이언트와 백엔드에서 클라이언트 스크립트를 직접 수정한다고 마지막으로 보고했습니다. 계속 지원되지만 제작자는 가능한 경우 로그인 핸드셰이크 전달 방식으로 이전하는 것이 좋습니다.",
        "Ce mod a indiqué en dernier lieu modifier directement les scripts pour ce client et ce backend. Il reste pris en charge, mais les auteurs devraient passer à la livraison lors de la connexion lorsque cela est possible.",
        "Dieser Mod hat zuletzt direkte Skriptänderungen für diesen Client und dieses Backend gemeldet. Er wird weiterhin unterstützt; Autoren sollten nach Möglichkeit auf die Bereitstellung beim Login-Handshake umstellen.",
        "Deze mod meldde voor deze client en backend dat hij clientscripts direct wijzigt. Dat blijft ondersteund, maar makers wordt aangeraden waar mogelijk over te stappen op levering via de login-handshake.",
        "По последнему отчёту этот мод напрямую изменяет скрипты для выбранного клиента и backend. Способ по-прежнему поддерживается, но авторам рекомендуется по возможности перейти на доставку через процедуру входа.",
    ),
})

_PHRASES.update({
    'Third-party mod update': (
        '第三方模组更新', 'サードパーティ製MODの更新', '타사 모드 업데이트',
        'Mise à jour d’un mod tiers', 'Update eines Drittanbieter-Mods',
        'Modupdate van een derde partij', 'Обновление стороннего мода',
    ),
    'THIRD-PARTY SOFTWARE': (
        '第三方软件', 'サードパーティ製ソフトウェア', '타사 소프트웨어',
        'LOGICIEL TIERS', 'DRITTANBIETER-SOFTWARE', 'SOFTWARE VAN DERDEN', 'СТОРОННЕЕ ПО',
    ),
    'Review before updating': (
        '更新前请确认', '更新前にご確認ください', '업데이트 전에 확인하세요',
        'À vérifier avant la mise à jour', 'Vor dem Update prüfen',
        'Controleer voordat je bijwerkt', 'Проверьте перед обновлением',
    ),
    'GitHub source': (
        'GitHub 来源', 'GitHubの配布元', 'GitHub 출처',
        'Source GitHub', 'GitHub-Quelle', 'GitHub-bron', 'Источник на GitHub',
    ),
    'Third-party code can be harmful': (
        '第三方代码可能有害', 'サードパーティ製のコードには危険が伴います', '타사 코드는 해로울 수 있습니다',
        'Le code tiers peut être dangereux', 'Drittanbieter-Code kann schädlich sein',
        'Code van derden kan schadelijk zijn', 'Сторонний код может быть опасен',
    ),
    'This will download and install a third-party mod update from the author’s GitHub repository, not a launcher update.': (
        '此操作将从作者的 GitHub 仓库下载并安装第三方模组更新，而非启动器更新。',
        '作者のGitHubリポジトリからサードパーティ製MODの更新をダウンロードしてインストールします。ランチャーの更新ではありません。',
        '제작자의 GitHub 저장소에서 타사 모드 업데이트를 다운로드하고 설치합니다. 런처 업데이트가 아닙니다.',
        'Cette action téléchargera et installera une mise à jour de mod tiers depuis le dépôt GitHub de son auteur, et non une mise à jour du lanceur.',
        'Hiermit wird ein Mod-Update aus dem GitHub-Repository des Drittanbieters heruntergeladen und installiert, kein Launcher-Update.',
        'Hiermee download en installeer je een modupdate van een derde partij uit de GitHub-repository van de maker. Dit is geen launcherupdate.',
        'Будет загружено и установлено обновление стороннего мода из репозитория автора на GitHub, а не обновление лаунчера.',
    ),
    'This launcher does not check mods for malicious code. A new release can contain harmful code, even if an earlier version was safe.': (
        '此启动器不会检查模组中是否包含恶意代码。即使旧版本是安全的，新版本也可能包含有害代码。',
        'このランチャーはMODに悪意のあるコードが含まれていないかを検査しません。以前のバージョンが安全でも、新しいリリースに有害なコードが含まれる可能性があります。',
        '이 런처는 모드에 악성 코드가 있는지 검사하지 않습니다. 이전 버전이 안전했더라도 새 릴리스에는 유해한 코드가 포함될 수 있습니다.',
        'Ce lanceur ne recherche pas de code malveillant dans les mods. Une nouvelle version peut contenir du code malveillant, même si une version précédente était sûre.',
        'Dieser Launcher prüft Mods nicht auf Schadcode. Eine neue Version kann schädlichen Code enthalten, auch wenn eine frühere Version sicher war.',
        'Deze launcher controleert mods niet op schadelijke code. Een nieuwe versie kan schadelijke code bevatten, ook als een eerdere versie veilig was.',
        'Этот лаунчер не проверяет моды на наличие вредоносного кода. Новый выпуск может содержать вредоносный код, даже если предыдущая версия была безопасной.',
    ),
    'Only continue if you trust the author and this release. No update starts until you confirm.': (
        '仅在您信任作者及此版本时继续。确认之前不会开始更新。',
        '作者とこのリリースを信頼できる場合のみ続行してください。確認するまで更新は始まりません。',
        '제작자와 이 릴리스를 신뢰하는 경우에만 계속하세요. 확인하기 전에는 업데이트가 시작되지 않습니다.',
        'Ne continuez que si vous faites confiance à l’auteur et à cette version. La mise à jour ne démarrera qu’après votre confirmation.',
        'Fahre nur fort, wenn du dem Autor und dieser Version vertraust. Das Update startet erst nach deiner Bestätigung.',
        'Ga alleen verder als je de maker en deze release vertrouwt. De update begint pas nadat je bevestigt.',
        'Продолжайте, только если доверяете автору и этому выпуску. Обновление начнётся лишь после вашего подтверждения.',
    ),
    'I understand — update mod': (
        '我已了解 — 更新模组', '理解した上でMODを更新', '이해했습니다 — 모드 업데이트',
        'Je comprends — mettre à jour', 'Verstanden — Mod aktualisieren',
        'Ik begrijp het — mod bijwerken', 'Понятно — обновить мод',
    ),
})

SOURCE_PHRASES = tuple(_PHRASES)
UI_PHRASES_BY_LANGUAGE = {
    language: {source: values[index] for source, values in _PHRASES.items()}
    for index, language in enumerate(LANGUAGE_CODES)
}
