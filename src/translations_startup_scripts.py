"""Reviewed Startup Scripts UI translations in every launcher language."""
from __future__ import annotations

LANGUAGE_CODES = ("zh_CN", "ja", "ko", "fr", "de", "nl", "ru")

_PHRASES = {
    "STARTUP SCRIPTS": ("启动脚本", "起動スクリプト", "시작 스크립트", "SCRIPTS DE DÉMARRAGE", "STARTSKRIPTE", "OPSTARTSCRIPTS", "СКРИПТЫ ЗАПУСКА"),
    "Run your own .bat files after the Game server is ready.": (
        "游戏服务器就绪后运行您自己的 .bat 文件。", "ゲームサーバーの準備完了後に任意の .bat ファイルを実行します。", "게임 서버가 준비되면 원하는 .bat 파일을 실행합니다.",
        "Exécutez vos fichiers .bat lorsque le serveur de jeu est prêt.", "Eigene .bat-Dateien ausführen, sobald der Spielserver bereit ist.", "Voer je eigen .bat-bestanden uit zodra de gameserver klaar is.", "Запускайте свои файлы .bat после готовности игрового сервера."),
    "+ Add .bat file": ("+ 添加 .bat 文件", "+ .bat ファイルを追加", "+ .bat 파일 추가", "+ Ajouter un fichier .bat", "+ .bat-Datei hinzufügen", "+ .bat-bestand toevoegen", "+ Добавить файл .bat"),
    "No startup scripts added.": ("尚未添加启动脚本。", "起動スクリプトは未登録です。", "추가된 시작 스크립트가 없습니다.", "Aucun script de démarrage ajouté.", "Keine Startskripte hinzugefügt.", "Nog geen opstartscripts toegevoegd.", "Скрипты запуска не добавлены."),
    "Scripts open in their own console and keep running when the launcher closes.": (
        "脚本在独立控制台中运行，启动器关闭后仍会继续运行。", "スクリプトは専用コンソールで実行され、ランチャーを閉じても動作を続けます。", "스크립트는 별도 콘솔에서 실행되며 런처를 닫아도 계속 실행됩니다.",
        "Les scripts s’ouvrent dans leur propre console et continuent après la fermeture du lanceur.", "Skripte laufen in einer eigenen Konsole und nach dem Schließen des Launchers weiter.", "Scripts openen in een eigen console en blijven actief als de launcher sluit.", "Скрипты открываются в отдельной консоли и продолжают работу после закрытия лаунчера."),
    "Status refers to the batch script, not any program it starts.": (
        "此状态表示批处理脚本的状态，而非它启动的程序的状态。", "この状態はバッチスクリプトの状態です。起動されたプログラムの状態ではありません。", "이 상태는 배치 스크립트의 상태이며 스크립트가 실행한 프로그램의 상태는 아닙니다.",
        "L’état concerne le script batch, pas les programmes qu’il lance.", "Der Status bezieht sich auf das Batchskript, nicht auf dadurch gestartete Programme.", "De status geldt voor het batchscript, niet voor de programma’s die het start.", "Статус относится к пакетному скрипту, а не к запущенным им программам."),
    "Start with server": ("随服务器启动", "サーバーと同時に起動", "서버와 함께 시작", "Démarrer avec le serveur", "Mit Server starten", "Starten met server", "Запускать с сервером"),
    "Run now": ("立即运行", "今すぐ実行", "지금 실행", "Exécuter", "Jetzt ausführen", "Nu uitvoeren", "Запустить сейчас"),
    "Edit": ("编辑", "編集", "편집", "Modifier", "Bearbeiten", "Bewerken", "Изменить"),
    "Remove": ("移除", "削除", "제거", "Retirer", "Entfernen", "Verwijderen", "Удалить"),
    "NOT STARTED": ("未启动", "未起動", "시작 안 됨", "NON DÉMARRÉ", "NICHT GESTARTET", "NIET GESTART", "НЕ ЗАПУЩЕН"),
    "RUNNING": ("运行中", "実行中", "실행 중", "EN COURS", "LÄUFT", "ACTIEF", "ВЫПОЛНЯЕТСЯ"),
    "COMPLETED": ("已完成", "完了", "완료", "TERMINÉ", "ABGESCHLOSSEN", "VOLTOOID", "ЗАВЕРШЁН"),
    "FAILED": ("失败", "失敗", "실패", "ÉCHEC", "FEHLGESCHLAGEN", "MISLUKT", "ОШИБКА"),
    "Add startup script": ("添加启动脚本", "起動スクリプトを追加", "시작 스크립트 추가", "Ajouter un script de démarrage", "Startskript hinzufügen", "Opstartscript toevoegen", "Добавить скрипт запуска"),
    "Edit startup script": ("编辑启动脚本", "起動スクリプトを編集", "시작 스크립트 편집", "Modifier le script de démarrage", "Startskript bearbeiten", "Opstartscript bewerken", "Изменить скрипт запуска"),
    "Name": ("名称", "名前", "이름", "Nom", "Name", "Naam", "Название"),
    "Batch file": ("批处理文件", "バッチファイル", "배치 파일", "Fichier batch", "Batchdatei", "Batchbestand", "Пакетный файл"),
    "Arguments": ("参数", "引数", "인수", "Arguments", "Argumente", "Argumenten", "Аргументы"),
    "Browse…": ("浏览…", "参照…", "찾아보기…", "Parcourir…", "Durchsuchen…", "Bladeren…", "Обзор…"),
    "Save": ("保存", "保存", "저장", "Enregistrer", "Speichern", "Opslaan", "Сохранить"),
    "Cancel": ("取消", "キャンセル", "취소", "Annuler", "Abbrechen", "Annuleren", "Отмена"),
    "Optional arguments, e.g. start --port 12345": (
        "可选参数，例如 start --port 12345", "任意の引数（例：start --port 12345）", "선택적 인수, 예: start --port 12345",
        "Arguments facultatifs, par ex. start --port 12345", "Optionale Argumente, z. B. start --port 12345", "Optionele argumenten, bijv. start --port 12345", "Необязательные аргументы, например start --port 12345"),
    "Batch files (*.bat)": ("批处理文件 (*.bat)", "バッチファイル (*.bat)", "배치 파일 (*.bat)", "Fichiers batch (*.bat)", "Batchdateien (*.bat)", "Batchbestanden (*.bat)", "Пакетные файлы (*.bat)"),
    "Select an EveJS folder in Settings first.": (
        "请先在设置中选择 EveJS 文件夹。", "先に設定で EveJS フォルダーを選択してください。", "먼저 설정에서 EveJS 폴더를 선택하세요.",
        "Sélectionnez d’abord un dossier EveJS dans les paramètres.", "Zuerst einen EveJS-Ordner in den Einstellungen auswählen.", "Selecteer eerst een EveJS-map in Instellingen.", "Сначала выберите папку EveJS в настройках."),
    "Choose an absolute .bat file path.": (
        "请选择 .bat 文件的绝对路径。", ".bat ファイルの絶対パスを指定してください。", ".bat 파일의 절대 경로를 선택하세요.",
        "Choisissez un chemin absolu vers un fichier .bat.", "Einen absoluten Pfad zu einer .bat-Datei auswählen.", "Kies een absoluut pad naar een .bat-bestand.", "Укажите абсолютный путь к файлу .bat."),
    "Enter a script name.": ("请输入脚本名称。", "スクリプト名を入力してください。", "스크립트 이름을 입력하세요.", "Saisissez un nom de script.", "Einen Skriptnamen eingeben.", "Voer een scriptnaam in.", "Введите название скрипта."),
    "Invalid script arguments.": ("脚本参数无效。", "スクリプトの引数が無効です。", "스크립트 인수가 올바르지 않습니다.", "Arguments de script non valides.", "Ungültige Skriptargumente.", "Ongeldige scriptargumenten.", "Недопустимые аргументы скрипта."),
    "Arguments cannot contain literal double quotes.": (
        "参数值不能包含字面双引号。", "引数の値に二重引用符そのものは含められません。", "인수 값에는 큰따옴표 문자를 포함할 수 없습니다.",
        "Les valeurs des arguments ne peuvent pas contenir de guillemets doubles littéraux.", "Argumentwerte dürfen keine wörtlichen doppelten Anführungszeichen enthalten.", "Argumentwaarden mogen geen letterlijke dubbele aanhalingstekens bevatten.", "Значения аргументов не могут содержать буквальные двойные кавычки."),
    "Invalid automatic startup setting.": ("自动启动设置无效。", "自動起動設定が無効です。", "자동 시작 설정이 올바르지 않습니다.", "Paramètre de démarrage automatique non valide.", "Ungültige Autostarteinstellung.", "Ongeldige instelling voor automatisch starten.", "Недопустимая настройка автозапуска."),
    "Invalid script identifier.": ("脚本标识符无效。", "スクリプト識別子が無効です。", "스크립트 식별자가 올바르지 않습니다.", "Identifiant de script non valide.", "Ungültige Skriptkennung.", "Ongeldige script-ID.", "Недопустимый идентификатор скрипта."),
    "Startup script settings could not be read.": (
        "无法读取启动脚本设置。", "起動スクリプトの設定を読み込めませんでした。", "시작 스크립트 설정을 읽을 수 없습니다.",
        "Impossible de lire les paramètres des scripts de démarrage.", "Startskripteinstellungen konnten nicht gelesen werden.", "De instellingen voor opstartscripts konden niet worden gelezen.", "Не удалось прочитать настройки скриптов запуска."),
    "An installation can have at most 64 startup scripts.": (
        "每个安装最多可添加 64 个启动脚本。", "1 つのインストールに登録できる起動スクリプトは最大 64 個です。", "설치당 시작 스크립트는 최대 64개까지 추가할 수 있습니다.",
        "Une installation peut contenir au maximum 64 scripts de démarrage.", "Pro Installation sind höchstens 64 Startskripte möglich.", "Per installatie kun je maximaal 64 opstartscripts toevoegen.", "Для одной установки можно добавить не более 64 скриптов запуска."),
    "Choose an existing .bat file.": ("请选择现有的 .bat 文件。", "既存の .bat ファイルを選択してください。", "기존 .bat 파일을 선택하세요.", "Choisissez un fichier .bat existant.", "Eine vorhandene .bat-Datei auswählen.", "Kies een bestaand .bat-bestand.", "Выберите существующий файл .bat."),
    "Startup script settings could not be saved.": (
        "无法保存启动脚本设置。", "起動スクリプトの設定を保存できませんでした。", "시작 스크립트 설정을 저장할 수 없습니다.",
        "Impossible d’enregistrer les paramètres des scripts de démarrage.", "Startskripteinstellungen konnten nicht gespeichert werden.", "De instellingen voor opstartscripts konden niet worden opgeslagen.", "Не удалось сохранить настройки скриптов запуска."),
}

SOURCE_PHRASES = tuple(_PHRASES)
UI_PHRASES_BY_LANGUAGE = {
    language: {source: values[index] for source, values in _PHRASES.items()}
    for index, language in enumerate(LANGUAGE_CODES)
}
