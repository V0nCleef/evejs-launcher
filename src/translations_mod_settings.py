"""Launcher-owned mod settings framing; author labels stay in their schema."""
from __future__ import annotations

# Each row follows LANGUAGE_CODES. Named fields are formatted after translation.
LANGUAGE_CODES = ("zh_CN", "ja", "ko", "fr", "de", "nl", "ru")
_PHRASES: dict[str, tuple[str, ...]] = {
    "This installer cannot preserve overlapping edits automatically. Nothing was removed. Ask the mod author for a compatible removal method, then retry.": (
        "此安装程序无法自动保留重叠的修改。未移除任何内容。请向模组作者咨询兼容的移除方法后重试。",
        "このインストーラーは重複する変更を自動的に保持できません。何も削除されていません。MOD作者に対応する削除方法を確認してから再試行してください。",
        "이 설치 프로그램은 겹치는 변경을 자동으로 보존할 수 없습니다. 아무것도 제거하지 않았습니다. 모드 제작자에게 호환되는 제거 방법을 문의한 후 다시 시도하세요.",
        "Cet installateur ne peut pas préserver automatiquement les modifications qui se chevauchent. Rien n’a été supprimé. Demandez une méthode de suppression compatible à l’auteur du mod, puis réessayez.",
        "Dieses Installationsprogramm kann überlappende Änderungen nicht automatisch erhalten. Es wurde nichts entfernt. Fragen Sie den Mod-Autor nach einer kompatiblen Entfernungsmethode und versuchen Sie es erneut.",
        "Dit installatieprogramma kan overlappende wijzigingen niet automatisch behouden. Er is niets verwijderd. Vraag de modmaker om een geschikte verwijdermethode en probeer het daarna opnieuw.",
        "Установщик не может автоматически сохранить пересекающиеся изменения. Ничего не удалено. Запросите у автора мода совместимый способ удаления и повторите попытку.",
    ),
    "Only a target hash is recorded. Replacement contents and a safe merge are unavailable.": (
        "仅记录了目标哈希值。无法提供替换内容或安全合并。", "記録されているのは対象ハッシュのみです。置換内容と安全なマージは利用できません。",
        "대상 해시만 기록되어 있습니다. 교체할 내용이나 안전한 병합을 제공할 수 없습니다.",
        "Seule une empreinte cible est enregistrée. Le contenu de remplacement et une fusion sûre ne sont pas disponibles.",
        "Nur ein Ziel-Hash ist gespeichert. Ersatzinhalt und eine sichere Zusammenführung sind nicht verfügbar.",
        "Alleen een doelhash is vastgelegd. De vervangende inhoud en een veilige samenvoeging zijn niet beschikbaar.",
        "Записан только целевой хеш. Содержимое замены и безопасное объединение недоступны.",
    ),
    "Binary file; text preview unavailable.": (
        "二进制文件；无法预览文本。", "バイナリファイルのためテキストを表示できません。", "바이너리 파일: 텍스트 미리보기를 사용할 수 없습니다.",
        "Fichier binaire : aperçu textuel indisponible.", "Binärdatei; keine Textvorschau verfügbar.",
        "Binair bestand; tekstvoorbeeld niet beschikbaar.", "Двоичный файл; текстовый просмотр недоступен.",
    ),
    "Configure {mod}": (
        "配置 {mod}", "{mod} の設定", "{mod} 설정", "Configurer {mod}",
        "{mod} konfigurieren", "{mod} instellen", "Настройка {mod}",
    ),
    "Restore Defaults": (
        "恢复默认值", "既定値に戻す", "기본값 복원", "Rétablir les valeurs par défaut",
        "Standardwerte wiederherstellen", "Standaardwaarden herstellen", "Восстановить значения по умолчанию",
    ),
    "Search settings...": (
        "搜索设置...", "設定を検索...", "설정 검색...", "Rechercher un réglage...",
        "Einstellungen suchen...", "Instellingen zoeken...", "Поиск настроек...",
    ),
    "Show advanced settings": (
        "显示高级设置", "詳細設定を表示", "고급 설정 표시", "Afficher les réglages avancés",
        "Erweiterte Einstellungen anzeigen", "Geavanceerde instellingen tonen", "Показать дополнительные настройки",
    ),
    "No settings match your search.": (
        "没有与搜索匹配的设置。", "検索条件に一致する設定はありません。", "검색과 일치하는 설정이 없습니다.",
        "Aucun réglage ne correspond à votre recherche.", "Keine Einstellungen entsprechen der Suche.",
        "Geen instellingen gevonden voor deze zoekopdracht.", "Настройки по вашему запросу не найдены.",
    ),
    "No settings are available.": (
        "没有可用的设置。", "設定項目はありません。", "사용 가능한 설정이 없습니다.",
        "Aucun réglage disponible.", "Keine Einstellungen verfügbar.",
        "Er zijn geen instellingen beschikbaar.", "Нет доступных настроек.",
    ),
    "Discard unsaved changes to these mod settings?": (
        "放弃对这些模组设置所做的未保存更改？", "このモッド設定への未保存の変更を破棄しますか？",
        "이 모드 설정의 저장하지 않은 변경 사항을 버릴까요?",
        "Abandonner les modifications non enregistrées de ces réglages du mod ?",
        "Ungespeicherte Änderungen an diesen Mod-Einstellungen verwerfen?",
        "Niet-opgeslagen wijzigingen aan deze modinstellingen weggooien?",
        "Отменить несохранённые изменения настроек мода?",
    ),
    "Fix the highlighted settings before saving.": (
        "请先修正标出的设置，再保存。", "保存する前に、強調表示された設定を修正してください。",
        "저장하기 전에 강조 표시된 설정을 수정하세요.", "Corrigez les réglages signalés avant d’enregistrer.",
        "Korrigiere die markierten Einstellungen vor dem Speichern.", "Corrigeer de gemarkeerde instellingen voordat je opslaat.",
        "Перед сохранением исправьте выделенные настройки.",
    ),
    "Saving settings...": (
        "正在保存设置...", "設定を保存中...", "설정 저장 중...", "Enregistrement des réglages...",
        "Einstellungen werden gespeichert...", "Instellingen opslaan...", "Сохранение настроек...",
    ),
    "Restart the game server to apply these changes.": (
        "重启游戏服务器以应用这些更改。", "変更を適用するにはゲームサーバーを再起動してください。",
        "변경 사항을 적용하려면 게임 서버를 다시 시작하세요.", "Redémarrez le serveur de jeu pour appliquer ces modifications.",
        "Starte den Spielserver neu, um diese Änderungen anzuwenden.", "Herstart de gameserver om deze wijzigingen toe te passen.",
        "Перезапустите игровой сервер, чтобы применить изменения.",
    ),
    "Restart the EVE client to apply these changes.": (
        "重启 EVE 客户端以应用这些更改。", "変更を適用するには EVE クライアントを再起動してください。",
        "변경 사항을 적용하려면 EVE 클라이언트를 다시 시작하세요.", "Redémarrez le client EVE pour appliquer ces modifications.",
        "Starte den EVE-Client neu, um diese Änderungen anzuwenden.", "Herstart de EVE-client om deze wijzigingen toe te passen.",
        "Перезапустите клиент EVE, чтобы применить изменения.",
    ),
    "Restart the launcher to apply these changes.": (
        "重启启动器以应用这些更改。", "変更を適用するにはランチャーを再起動してください。",
        "변경 사항을 적용하려면 런처를 다시 시작하세요.", "Redémarrez le lanceur pour appliquer ces modifications.",
        "Starte den Launcher neu, um diese Änderungen anzuwenden.", "Herstart de launcher om deze wijzigingen toe te passen.",
        "Перезапустите лаунчер, чтобы применить изменения.",
    ),
    "A value is required.": (
        "必须填写一个值。", "値を入力してください。", "값을 입력해야 합니다.", "Une valeur est requise.",
        "Ein Wert ist erforderlich.", "Een waarde is verplicht.", "Необходимо указать значение.",
    ),
    "Choose on or off.": (
        "请选择开启或关闭。", "オンまたはオフを選択してください。", "켜짐 또는 꺼짐을 선택하세요.",
        "Choisissez activé ou désactivé.", "Wähle Ein oder Aus.", "Kies aan of uit.", "Выберите «включено» или «выключено».",
    ),
    "Enter a whole number.": (
        "请输入整数。", "整数を入力してください。", "정수를 입력하세요.", "Saisissez un nombre entier.",
        "Gib eine ganze Zahl ein.", "Voer een geheel getal in.", "Введите целое число.",
    ),
    "Enter a finite number.": (
        "请输入有限数值。", "有限の数値を入力してください。", "유한한 수를 입력하세요.", "Saisissez un nombre fini.",
        "Gib eine endliche Zahl ein.", "Voer een eindig getal in.", "Введите конечное число.",
    ),
    "Enter text.": (
        "请输入文本。", "テキストを入力してください。", "텍스트를 입력하세요.", "Saisissez du texte.",
        "Gib Text ein.", "Voer tekst in.", "Введите текст.",
    ),
    "Choose one of the available options.": (
        "请选择一个可用选项。", "利用可能な選択肢から選んでください。", "사용 가능한 옵션 중 하나를 선택하세요.",
        "Choisissez l’une des options disponibles.", "Wähle eine der verfügbaren Optionen.",
        "Kies een van de beschikbare opties.", "Выберите один из доступных вариантов.",
    ),
    "This setting type is not supported.": (
        "不支持此设置类型。", "この設定の種類には対応していません。", "이 설정 유형은 지원되지 않습니다.",
        "Ce type de réglage n’est pas pris en charge.", "Dieser Einstellungstyp wird nicht unterstützt.",
        "Dit type instelling wordt niet ondersteund.", "Этот тип настройки не поддерживается.",
    ),
    "The minimum value is {value}.": (
        "最小值为 {value}。", "最小値は {value} です。", "최솟값은 {value}입니다.", "La valeur minimale est {value}.",
        "Der Mindestwert ist {value}.", "De minimumwaarde is {value}.", "Минимальное значение: {value}.",
    ),
    "The maximum value is {value}.": (
        "最大值为 {value}。", "最大値は {value} です。", "최댓값은 {value}입니다.", "La valeur maximale est {value}.",
        "Der Höchstwert ist {value}.", "De maximumwaarde is {value}.", "Максимальное значение: {value}.",
    ),
    "Use at most {count} characters.": (
        "最多可输入 {count} 个字符。", "{count} 文字以内で入力してください。", "최대 {count}자까지 입력하세요.",
        "Utilisez au maximum {count} caractères.", "Verwende höchstens {count} Zeichen.",
        "Gebruik maximaal {count} tekens.", "Используйте не более {count} символов.",
    ),
    "The settings form has changed. Reopen it and try again.": (
        "设置表单已更改。请重新打开后再试。", "設定フォームが変更されました。開き直して再試行してください。",
        "설정 양식이 변경되었습니다. 다시 열고 시도하세요.", "Le formulaire de réglages a changé. Rouvrez-le et réessayez.",
        "Das Einstellungsformular wurde geändert. Öffne es erneut und versuche es noch einmal.",
        "Het instellingenformulier is gewijzigd. Open het opnieuw en probeer het nogmaals.",
        "Форма настроек изменилась. Откройте её заново и повторите попытку.",
    ),
}

_PHRASES.update({
    "Configure": ("配置", "設定", "설정", "Configurer", "Konfigurieren", "Instellen", "Настроить"),
    "Actions": ("操作", "操作", "작업", "Actions", "Aktionen", "Acties", "Действия"),
    "Install / Update": ("安装 / 更新", "インストール / 更新", "설치 / 업데이트", "Installer / Mettre à jour", "Installieren / Aktualisieren", "Installeren / Bijwerken", "Установить / Обновить"),
    "Verify": ("验证", "検証", "검증", "Vérifier", "Prüfen", "Controleren", "Проверить"),
    "Recover": ("恢复", "復旧", "복구", "Récupérer", "Wiederherstellen", "Herstellen", "Восстановить"),
    "Add ZIP": ("添加 ZIP", "ZIP を追加", "ZIP 추가", "Ajouter un ZIP", "ZIP hinzufügen", "ZIP toevoegen", "Добавить ZIP"),
    "Add Folder": ("添加文件夹", "フォルダーを追加", "폴더 추가", "Ajouter un dossier", "Ordner hinzufügen", "Map toevoegen", "Добавить папку"),
    "Add Mod ZIP": ("添加模组 ZIP", "モッド ZIP を追加", "모드 ZIP 추가", "Ajouter un mod ZIP", "Mod-ZIP hinzufügen", "Mod-ZIP toevoegen", "Добавить ZIP мода"),
    "Add Mod Folder": ("添加模组文件夹", "モッドフォルダーを追加", "모드 폴더 추가", "Ajouter un dossier de mod", "Mod-Ordner hinzufügen", "Modmap toevoegen", "Добавить папку мода"),
    "ZIP archives (*.zip)": ("ZIP 压缩包 (*.zip)", "ZIP アーカイブ (*.zip)", "ZIP 압축 파일 (*.zip)", "Archives ZIP (*.zip)", "ZIP-Archive (*.zip)", "ZIP-archieven (*.zip)", "Архивы ZIP (*.zip)"),
    "Undo Removal": ("撤销移除", "削除を元に戻す", "제거 취소", "Annuler le retrait", "Entfernung rückgängig machen", "Verwijdering ongedaan maken", "Отменить удаление"),
    "Recover Interrupted Operation": ("恢复中断的操作", "中断した操作を復旧", "중단된 작업 복구", "Récupérer l’opération interrompue", "Unterbrochenen Vorgang wiederherstellen", "Onderbroken actie herstellen", "Восстановить прерванную операцию"),
    "Choose a removed mod": ("选择已移除的模组", "削除したモッドを選択", "제거된 모드 선택", "Choisir un mod retiré", "Entfernten Mod auswählen", "Kies een verwijderde mod", "Выберите удалённый мод"),
    "Move mod earlier": ("提前加载模组", "読み込み順を前へ", "모드 로드 순서 앞으로", "Charger le mod plus tôt", "Mod früher laden", "Mod eerder laden", "Загружать мод раньше"),
    "Move mod later": ("延后加载模组", "読み込み順を後ろへ", "모드 로드 순서 뒤로", "Charger le mod plus tard", "Mod später laden", "Mod later laden", "Загружать мод позже"),
    "CLEANUP PENDING": ("等待清理", "クリーンアップ待機中", "정리 대기 중", "NETTOYAGE EN ATTENTE", "BEREINIGUNG AUSSTEHEND", "OPRUIMEN IN AFWACHTING", "ОЖИДАЕТ ОЧИСТКИ"),
    "CONFIGURED ON": ("已设为启用", "有効に設定済み", "활성화 설정됨", "ACTIVÉ DANS LA CONFIGURATION", "AKTIVIERT", "INGESCHAKELD", "ВКЛЮЧЁН В НАСТРОЙКАХ"),
    "CONFIGURED OFF": ("已设为禁用", "無効に設定済み", "비활성화 설정됨", "DÉSACTIVÉ DANS LA CONFIGURATION", "DEAKTIVIERT", "UITGESCHAKELD", "ВЫКЛЮЧЕН В НАСТРОЙКАХ"),
    "Mod Settings": ("模组设置", "モッド設定", "모드 설정", "Réglages du mod", "Mod-Einstellungen", "Modinstellingen", "Настройки мода"),
    "Mod Operation": ("模组操作", "モッド操作", "모드 작업", "Opération du mod", "Mod-Vorgang", "Modactie", "Операция с модом"),
    "Global settings": ("全局设置", "共通設定", "공통 설정", "Réglages globaux", "Globale Einstellungen", "Algemene instellingen", "Общие настройки"),
    "Profile settings": ("配置文件设置", "プロファイル設定", "프로필 설정", "Réglages du profil", "Profileinstellungen", "Profielinstellingen", "Настройки профиля"),
    "Settings scope": ("设置范围", "設定の適用範囲", "설정 범위", "Portée des réglages", "Geltungsbereich", "Bereik van instellingen", "Область настроек"),
    "Choose a profile": ("选择配置文件", "プロファイルを選択", "프로필 선택", "Choisir un profil", "Profil auswählen", "Kies een profiel", "Выберите профиль"),
    "Profile: {profile}": ("配置文件：{profile}", "プロファイル: {profile}", "프로필: {profile}", "Profil : {profile}", "Profil: {profile}", "Profiel: {profile}", "Профиль: {profile}"),
    "Mod authoring guide": ("模组编写指南", "モッド制作ガイド", "모드 제작 가이드", "Guide de création de mods", "Anleitung für Mod-Autoren", "Handleiding voor modmakers", "Руководство автора мода"),
    "Find in this page": ("在此页查找", "ページ内を検索", "이 페이지에서 찾기", "Rechercher dans cette page", "Auf dieser Seite suchen", "Zoeken op deze pagina", "Найти на странице"),
    "Find next": ("查找下一个", "次を検索", "다음 찾기", "Rechercher le suivant", "Weitersuchen", "Volgende zoeken", "Найти далее"),
    "Open the bundled mod-authoring guide.": ("打开随启动器提供的模组编写指南。", "同梱のモッド制作ガイドを開きます。", "포함된 모드 제작 가이드를 엽니다.", "Ouvrir le guide de création de mods inclus.", "Die mitgelieferte Mod-Anleitung öffnen.", "Open de meegeleverde handleiding voor modmakers.", "Открыть встроенное руководство автора мода."),
    "Move this mod to recovery storage. You can undo removal.": ("将此模组移至恢复存储。可撤销移除。", "モッドを復旧用の保存先に移動します。削除は元に戻せます。", "이 모드를 복구 저장소로 이동합니다. 제거를 취소할 수 있습니다.", "Déplacer ce mod vers le stockage de récupération. Le retrait est réversible.", "Diesen Mod zur Wiederherstellung aufbewahren. Die Entfernung lässt sich rückgängig machen.", "Verplaats deze mod naar de herstelopslag. Je kunt de verwijdering ongedaan maken.", "Переместить мод в хранилище восстановления. Удаление можно отменить."),
    "Wait for the active operation to finish before changing mods.": ("请等待当前操作完成后再更改模组。", "実行中の操作が完了してからモッドを変更してください。", "진행 중인 작업이 끝난 후 모드를 변경하세요.", "Attendez la fin de l’opération avant de modifier les mods.", "Warte auf den laufenden Vorgang, bevor du Mods änderst.", "Wacht tot de huidige actie klaar is voordat je mods wijzigt.", "Дождитесь завершения текущей операции перед изменением модов."),
    "No profiles are available for this EveJS installation.": ("此 EveJS 安装没有可用的配置文件。", "この EveJS 環境には利用可能なプロファイルがありません。", "이 EveJS 설치에 사용 가능한 프로필이 없습니다.", "Aucun profil disponible pour cette installation EveJS.", "Für diese EveJS-Installation sind keine Profile verfügbar.", "Er zijn geen profielen beschikbaar voor deze EveJS-installatie.", "Для этой установки EveJS нет доступных профилей."),
    "Remove {mod}? Its folder will be kept for Undo Removal. Private settings are kept; shared changes are restored where ownership is recorded.": ("移除 {mod}？其文件夹将保留以便撤销移除。保留私有设置，并根据归属记录恢复共享更改。", "{mod} を削除しますか？フォルダーは復旧用に保存されます。個別設定を保持し、所有記録のある共有変更を復元します。", "{mod}을(를) 제거할까요? 폴더는 제거 취소를 위해 보관됩니다. 개인 설정을 유지하고 소유권이 기록된 공유 변경을 복원합니다.", "Retirer {mod} ? Son dossier sera conservé pour annuler le retrait. Les réglages privés sont conservés ; les changements partagés sont restaurés selon les enregistrements de propriété.", "{mod} entfernen? Der Ordner bleibt zur Wiederherstellung erhalten. Private Einstellungen bleiben bestehen; gemeinsame Änderungen werden anhand der Eigentumsaufzeichnungen zurückgesetzt.", "{mod} verwijderen? De map blijft bewaard zodat je dit ongedaan kunt maken. Privé-instellingen blijven behouden; gedeelde wijzigingen worden hersteld waar het eigendom is vastgelegd.", "Удалить {mod}? Папка будет сохранена для отмены удаления. Личные настройки сохранятся; общие изменения будут восстановлены по записям владения."),
})

_PHRASES.update({
    "Operation In Progress": ("操作进行中", "操作中", "작업 진행 중", "Opération en cours", "Vorgang läuft", "Actie bezig", "Операция выполняется"),
    "Remove Mod": ("移除模组", "モッドを削除", "모드 제거", "Retirer le mod", "Mod entfernen", "Mod verwijderen", "Удалить мод"),
    "Stop the game server before removing this mod, then try again.": ("请先停止游戏服务器，再重试移除此模组。", "ゲームサーバーを停止してから、モッドの削除を再試行してください。", "게임 서버를 중지한 후 모드 제거를 다시 시도하세요.", "Arrêtez le serveur de jeu, puis réessayez de retirer ce mod.", "Stoppen Sie den Spielserver und versuchen Sie dann erneut, diesen Mod zu entfernen.", "Stop de gameserver en probeer deze mod daarna opnieuw te verwijderen.", "Остановите игровой сервер и повторите удаление мода."),
    "The selected profile path is invalid.": ("所选配置路径无效。", "選択したプロファイルのパスが無効です。", "선택한 프로필 경로가 올바르지 않습니다.", "Le chemin du profil sélectionné est invalide.", "Der Pfad des ausgewählten Profils ist ungültig.", "Het geselecteerde profielpad is ongeldig.", "Путь выбранного профиля недопустим."),
    "The selected EveJS installation changed. Refresh Mods and try again.": ("所选 EveJS 安装已更改。请刷新模组并重试。", "選択した EveJS のインストール先が変わりました。モッドを更新して再試行してください。", "선택한 EveJS 설치가 변경되었습니다. 모드 목록을 새로 고친 후 다시 시도하세요.", "L’installation EveJS sélectionnée a changé. Actualisez les mods et réessayez.", "Die ausgewählte EveJS-Installation hat sich geändert. Aktualisieren Sie die Mod-Liste und versuchen Sie es erneut.", "De geselecteerde EveJS-installatie is gewijzigd. Vernieuw de modlijst en probeer het opnieuw.", "Выбранная установка EveJS изменилась. Обновите список модов и повторите попытку."),
    "Wait for the active operation to finish before installing a launcher update.": ("请等待当前操作完成后再安装启动器更新。", "実行中の操作が完了してからランチャーを更新してください。", "진행 중인 작업이 끝난 후 런처 업데이트를 설치하세요.", "Attendez la fin de l’opération en cours avant de mettre à jour le lanceur.", "Warten Sie, bis der laufende Vorgang abgeschlossen ist, bevor Sie den Launcher aktualisieren.", "Wacht tot de huidige actie klaar is voordat je de launcher bijwerkt.", "Дождитесь завершения текущей операции перед обновлением лаунчера."),
    "Wait for the launcher update to finish before changing the runtime.": ("请等待启动器更新完成后再更改运行环境。", "ランチャーの更新が完了してから実行環境を変更してください。", "런처 업데이트가 끝난 후 실행 환경을 변경하세요.", "Attendez la fin de la mise à jour du lanceur avant de modifier l’environnement d’exécution.", "Warten Sie auf den Abschluss des Launcher-Updates, bevor Sie die Laufzeitumgebung ändern.", "Wacht tot de launcherupdate klaar is voordat je de runtime wijzigt.", "Дождитесь завершения обновления лаунчера перед изменением среды выполнения."),
    "The selected root or DLSS5 package changed. Refresh Mods first.": ("所选根目录或 DLSS5 包已更改。请先刷新模组。", "選択したルートまたは DLSS5 パッケージが変わりました。先にモッドを更新してください。", "선택한 루트 또는 DLSS5 패키지가 변경되었습니다. 먼저 모드 목록을 새로 고치세요.", "Le dossier racine ou le paquet DLSS5 sélectionné a changé. Actualisez d’abord les mods.", "Das ausgewählte Stammverzeichnis oder DLSS5-Paket hat sich geändert. Aktualisieren Sie zuerst die Mod-Liste.", "De geselecteerde hoofdmap of het DLSS5-pakket is gewijzigd. Vernieuw eerst de modlijst.", "Выбранный корневой каталог или пакет DLSS5 изменился. Сначала обновите список модов."),
    "The selected EveJS root or client changed. Refresh Mods and try again.": ("所选 EveJS 根目录或客户端已更改。请刷新模组并重试。", "選択した EveJS ルートまたはクライアントが変わりました。モッドを更新して再試行してください。", "선택한 EveJS 루트 또는 클라이언트가 변경되었습니다. 모드 목록을 새로 고친 후 다시 시도하세요.", "Le dossier EveJS ou le client sélectionné a changé. Actualisez les mods et réessayez.", "Das ausgewählte EveJS-Verzeichnis oder der Client hat sich geändert. Aktualisieren Sie die Mod-Liste und versuchen Sie es erneut.", "De geselecteerde EveJS-map of client is gewijzigd. Vernieuw de modlijst en probeer het opnieuw.", "Выбранный каталог EveJS или клиент изменился. Обновите список модов и повторите попытку."),
    "Another operation started before uninstall could begin. Nothing was changed.": ("卸载开始前有其他操作启动。未进行任何更改。", "アンインストールの開始前に別の操作が始まりました。変更はありません。", "제거가 시작되기 전에 다른 작업이 시작되었습니다. 변경된 사항은 없습니다.", "Une autre opération a commencé avant la désinstallation. Aucune modification n’a été effectuée.", "Vor Beginn der Deinstallation wurde ein anderer Vorgang gestartet. Es wurde nichts geändert.", "Er is een andere actie gestart voordat de verwijdering kon beginnen. Er is niets gewijzigd.", "До начала удаления запустилась другая операция. Ничего не изменено."),
})

_PHRASES.update({
    "Review Mod Conflict": ("检查模组冲突", "モッドの競合を確認", "모드 충돌 검토", "Examiner le conflit de mods", "Mod-Konflikt prüfen", "Modconflict bekijken", "Проверить конфликт модов"),
    "Keep local edits": ("保留本地编辑", "ローカルの編集を保持", "로컬 수정 유지", "Conserver les modifications locales", "Lokale Änderungen behalten", "Lokale wijzigingen behouden", "Сохранить локальные изменения"),
    "Restore remaining mod settings": ("恢复其余模组设置", "残るモッドの設定を復元", "남은 모드 설정 복원", "Restaurer les réglages des mods restants", "Einstellungen verbleibender Mods wiederherstellen", "Instellingen van resterende mods herstellen", "Восстановить настройки оставшихся модов"),
    "Current file": ("当前文件", "現在のファイル", "현재 파일", "Fichier actuel", "Aktuelle Datei", "Huidig bestand", "Текущий файл"),
    "After this action": ("操作后", "操作後", "작업 후", "Après cette opération", "Nach diesem Vorgang", "Na deze actie", "После операции"),
    "Apply reviewed changes": ("应用已检查的更改", "確認した変更を適用", "검토한 변경 적용", "Appliquer les changements examinés", "Geprüfte Änderungen anwenden", "Bekeken wijzigingen toepassen", "Применить проверенные изменения"),
    "File does not exist": ("文件不存在", "ファイルは存在しません", "파일이 없습니다", "Le fichier n’existe pas", "Datei existiert nicht", "Bestand bestaat niet", "Файл не существует"),
    "Preview truncated; the full file is preserved.": ("预览已截断；完整文件会保留。", "プレビューは省略されています。ファイル全体は保持されます。", "미리보기가 잘렸습니다. 전체 파일은 보존됩니다.", "Aperçu tronqué ; le fichier complet est conservé.", "Vorschau gekürzt; die vollständige Datei bleibt erhalten.", "Voorbeeld ingekort; het volledige bestand blijft bewaard.", "Предпросмотр сокращён; полный файл сохранён."),
    "Choose how to handle local edits. Shared settings from other mods remain recorded. Files are backed up before replacement.": ("选择如何处理本地编辑。其他模组的共享设置仍保留归属记录。替换文件前会创建备份。", "ローカルの編集の扱いを選択してください。他のモッドの共有設定の記録は保持されます。置換前にファイルをバックアップします。", "로컬 수정 처리 방법을 선택하세요. 다른 모드의 공유 설정 기록은 유지됩니다. 파일 교체 전에 백업합니다.", "Choisissez comment traiter les modifications locales. Les réglages partagés des autres mods restent enregistrés. Les fichiers sont sauvegardés avant remplacement.", "Wählen Sie den Umgang mit lokalen Änderungen. Gemeinsame Einstellungen anderer Mods bleiben erfasst. Dateien werden vor dem Ersetzen gesichert.", "Kies hoe lokale wijzigingen worden behandeld. Gedeelde instellingen van andere mods blijven geregistreerd. Bestanden worden vóór vervanging geback-upt.", "Выберите способ обработки локальных изменений. Записи общих настроек других модов сохранятся. Перед заменой создаётся резервная копия файлов."),
})

_PHRASES.update({
    "Keep current settings": ("保留当前设置", "現在の設定を保持", "현재 설정 유지", "Conserver les réglages actuels", "Aktuelle Einstellungen behalten", "Huidige instellingen behouden", "Сохранить текущие настройки"),
    "Apply my settings": ("应用我的设置", "自分の設定を適用", "내 설정 적용", "Appliquer mes réglages", "Meine Einstellungen anwenden", "Mijn instellingen toepassen", "Применить мои настройки"),
})

_PHRASES.update({
    "Shared EveJS GameStore data cannot be used as an activation setting.": (
        "共享 EveJS GameStore 数据不能用作启用设置。",
        "共有の EveJS GameStore データを有効化設定として使用することはできません。",
        "공유 EveJS GameStore 데이터는 활성화 설정으로 사용할 수 없습니다.",
        "Les données partagées EveJS GameStore ne peuvent pas servir de réglage d’activation.",
        "Gemeinsame EveJS-GameStore-Daten können nicht als Aktivierungseinstellung verwendet werden.",
        "Gedeelde EveJS GameStore-gegevens kunnen niet als activeringsinstelling worden gebruikt.",
        "Общие данные EveJS GameStore нельзя использовать как настройку включения."
    ),
})

_PHRASES.update({
    "The removed mod's activation setting changed. Disable it before restoring its folder.": (
        "已移除模组的启用设置已更改。恢复其文件夹前，请先将其禁用。",
        "削除したMODの有効化設定が変更されています。フォルダーを復元する前に無効にしてください。",
        "제거한 모드의 활성화 설정이 변경되었습니다. 폴더를 복원하기 전에 비활성화하세요.",
        "Le réglage d’activation du mod supprimé a changé. Désactivez-le avant de restaurer son dossier.",
        "Die Aktivierungseinstellung des entfernten Mods wurde geändert. Deaktivieren Sie ihn, bevor Sie seinen Ordner wiederherstellen.",
        "De activeringsinstelling van de verwijderde mod is gewijzigd. Schakel deze uit voordat je de map herstelt.",
        "Настройка включения удалённого мода изменена. Отключите его перед восстановлением папки."
    ),
    "The mod configuration is too large.": (
        "模组配置过大。", "MOD設定が大きすぎます。", "모드 설정 파일이 너무 큽니다.",
        "La configuration du mod est trop volumineuse.", "Die Mod-Konfiguration ist zu groß.",
        "De modconfiguratie is te groot.", "Конфигурация мода слишком велика."
    ),
})

_PHRASES.update({
    "Keep current files": ("保留当前文件", "現在のファイルを保持", "현재 파일 유지", "Conserver les fichiers actuels", "Aktuelle Dateien behalten", "Huidige bestanden behouden", "Сохранить текущие файлы"),
    "Apply mod changes": ("应用模组修改", "MODの変更を適用", "모드 변경 적용", "Appliquer les modifications du mod", "Mod-Änderungen anwenden", "Modwijzigingen toepassen", "Применить изменения мода"),
    "Binary file": ("二进制文件", "バイナリファイル", "바이너리 파일", "Fichier binaire", "Binärdatei", "Binair bestand", "Двоичный файл"),
    "bytes": ("字节", "バイト", "바이트", "octets", "Bytes", "bytes", "байт"),
    "This mod's proposed file changes need review.": (
        "此模组提出的文件修改需要审核。", "このMODが提案するファイル変更の確認が必要です。",
        "이 모드가 제안한 파일 변경을 검토해야 합니다.", "Les modifications de fichiers proposées par ce mod doivent être examinées.",
        "Die vorgeschlagenen Dateiänderungen dieses Mods müssen geprüft werden.", "De voorgestelde bestandswijzigingen van deze mod moeten worden beoordeeld.",
        "Необходимо проверить изменения файлов, предлагаемые этим модом."),
    "Review this mod's proposed changes. Keeping current files cancels this action. Replaced files are backed up.": (
        "审核此模组提出的修改。保留当前文件将取消此操作。被替换的文件会备份。",
        "このMODが提案する変更を確認してください。現在のファイルを保持すると、この操作はキャンセルされます。置き換えるファイルはバックアップされます。",
        "이 모드가 제안한 변경을 검토하세요. 현재 파일을 유지하면 이 작업이 취소됩니다. 교체되는 파일은 백업됩니다.",
        "Examinez les modifications proposées par ce mod. Conserver les fichiers actuels annule cette action. Les fichiers remplacés sont sauvegardés.",
        "Prüfen Sie die vorgeschlagenen Änderungen dieses Mods. Das Beibehalten der aktuellen Dateien bricht diese Aktion ab. Ersetzte Dateien werden gesichert.",
        "Beoordeel de voorgestelde wijzigingen van deze mod. Huidige bestanden behouden annuleert deze actie. Vervangen bestanden worden geback-upt.",
        "Проверьте изменения, предлагаемые этим модом. Сохранение текущих файлов отменяет действие. Для заменяемых файлов создаются резервные копии."),
})

_PHRASES.update({
    "Source overlay packages must declare install, prepare_disable and prepare_remove.": (
        "源码覆盖包必须声明 install、prepare_disable 和 prepare_remove。",
        "ソース変更パッケージには install、prepare_disable、prepare_remove の宣言が必要です。",
        "소스 변경 패키지는 install, prepare_disable, prepare_remove를 선언해야 합니다.",
        "Les paquets de modification du code source doivent déclarer install, prepare_disable et prepare_remove.",
        "Quellcode-Änderungspakete müssen install, prepare_disable und prepare_remove deklarieren.",
        "Pakketten die bronbestanden wijzigen moeten install, prepare_disable en prepare_remove declareren.",
        "Пакеты изменений исходного кода должны объявлять install, prepare_disable и prepare_remove."
    ),
})

_PHRASES.update({
    "MAKE A MOD": ("制作模组", "MOD を作る", "모드 만들기", "CRÉER UN MOD", "MOD ERSTELLEN", "MAAK EEN MOD", "СОЗДАТЬ МОД"),
    "Mod integration guide": ("模组集成指南", "MOD 連携ガイド", "모드 연동 가이드", "Guide d’intégration des mods", "Mod-Integrationsanleitung", "Handleiding voor modintegratie", "Руководство по интеграции модов"),
    "Open the mod integration guide": ("打开模组集成指南", "MOD 連携ガイドを開く", "모드 연동 가이드 열기", "Ouvrir le guide d’intégration des mods", "Mod-Integrationsanleitung öffnen", "Open de handleiding voor modintegratie", "Открыть руководство по интеграции модов"),
    "Build a mod with launcher support. Follow the illustrated guide.": (
        "制作支持启动器的模组。请参阅图文指南。",
        "ランチャー対応の MOD を作りましょう。画像付きガイドをご覧ください。",
        "런처를 지원하는 모드를 만들어 보세요. 그림 안내를 참고하세요.",
        "Créez un mod compatible avec le lanceur. Suivez le guide illustré.",
        "Erstelle einen Mod mit Launcher-Unterstützung. Folge der bebilderten Anleitung.",
        "Maak een mod met launcherondersteuning. Volg de handleiding met afbeeldingen.",
        "Создайте мод с поддержкой лаунчера. Следуйте иллюстрированному руководству."
    ),
    "Read the illustrated guide in your language, without leaving the launcher.": (
        "在启动器内阅读你所选语言的图文指南。",
        "ランチャー内で、選んだ言語の画像付きガイドを読めます。",
        "런처 안에서 원하는 언어로 그림 안내를 읽으세요.",
        "Lisez le guide illustré dans votre langue, sans quitter le lanceur.",
        "Lies die bebilderte Anleitung in deiner Sprache direkt im Launcher.",
        "Lees de handleiding met afbeeldingen in je eigen taal, direct in de launcher.",
        "Читайте руководство с иллюстрациями на своём языке прямо в лаунчере."
    ),
})

SOURCE_PHRASES = tuple(_PHRASES)
UI_PHRASES_BY_LANGUAGE = {
    language: {source: values[index] for source, values in _PHRASES.items()}
    for index, language in enumerate(LANGUAGE_CODES)
}
