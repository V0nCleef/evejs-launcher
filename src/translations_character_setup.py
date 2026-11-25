"""Character setup choices shared by the supported language catalogs."""
from __future__ import annotations

_LANGUAGES = ("zh_CN", "ja", "ko", "fr", "de", "nl", "ru")
_ROWS: dict[str, tuple[str, ...]] = {
    "Simple New Character": (
        "轻松创建角色", "かんたんキャラクター作成", "간편 캐릭터 생성",
        "Création facile de personnage", "Einfache Charaktererstellung",
        "Eenvoudig personage aanmaken", "Простое создание персонажа",
    ),
    "Advanced Character Create": (
        "高级角色创建", "詳細なキャラクター作成", "고급 캐릭터 생성",
        "Création avancée de personnage", "Erweiterte Charaktererstellung",
        "Geavanceerd personage aanmaken", "Расширенное создание персонажа",
    ),
    "Start your journey in-game.": (
        "在游戏中开启旅程。", "ゲーム内で旅を始めましょう。", "게임에서 여정을 시작하세요.",
        "Commencez votre aventure en jeu.", "Beginnen Sie Ihre Reise im Spiel.",
        "Begin je avontuur in het spel.", "Начните своё путешествие в игре.",
    ),
    "Configure your pilot before launch.": (
        "启动前配置飞行员。", "起動前にパイロットを設定します。", "실행 전에 파일럿을 설정하세요.",
        "Configurez votre pilote avant le lancement.", "Konfigurieren Sie Ihren Piloten vor dem Start.",
        "Stel je piloot in voordat je begint.", "Настройте пилота перед запуском.",
    ),
    "SERVER STAYS ONLINE": (
        "服务器保持在线", "サーバーは稼働したまま", "서버 계속 온라인",
        "SERVEUR TOUJOURS ACTIF", "SERVER BLEIBT ONLINE",
        "SERVER BLIJFT ONLINE", "СЕРВЕР ОСТАЁТСЯ В СЕТИ",
    ),
    "OFFLINE SETUP": (
        "离线设置", "オフライン設定", "오프라인 설정", "CONFIGURATION HORS LIGNE",
        "OFFLINE-EINRICHTUNG", "OFFLINE INSTELLEN", "НАСТРОЙКА ОФЛАЙН",
    ),
    "ACCOUNT LOGIN": (
        "账号登录", "アカウントログイン", "계정 로그인", "CONNEXION AU COMPTE",
        "KONTOANMELDUNG", "INLOGGEN MET ACCOUNT", "ВХОД В УЧЁТНУЮ ЗАПИСЬ",
    ),
    "Character setup method": (
        "角色创建方式", "キャラクターの作成方法", "캐릭터 생성 방법",
        "Méthode de création du personnage", "Methode zur Charaktererstellung",
        "Manier van personage aanmaken", "Способ создания персонажа",
    ),
    "Automatic starter character — services offline": (
        "自动创建初始角色 — 服务离线", "初期キャラクターを自動作成 — サービスを停止",
        "시작 캐릭터 자동 생성 — 서비스 오프라인",
        "Personnage de départ automatique — services hors ligne",
        "Startcharakter automatisch erstellen — Dienste offline",
        "Automatisch startpersonage — diensten offline",
        "Автоматическое создание персонажа — службы отключены",
    ),
    "Create in-game — keep services running": (
        "在游戏中创建 — 保持服务运行", "ゲーム内で作成 — サービスの稼働を維持",
        "게임에서 생성 — 서비스 계속 실행",
        "Créer en jeu — garder les services actifs",
        "Im Spiel erstellen — Dienste weiterlaufen lassen",
        "In het spel aanmaken — diensten blijven draaien",
        "Создать в игре — оставить службы работающими",
    ),
    "Launch the EVE client while game and market services keep running. "
    "Sign in with the account name below, then create your character "
    "in-game. The server must allow automatic account creation.": (
        "启动 EVE 客户端，游戏和市场服务将继续运行。使用下方账号名称登录，然后在游戏中创建角色。服务器必须允许自动创建账号。",
        "ゲームとマーケットのサービスを稼働させたまま EVE クライアントを起動します。下のアカウント名でログインし、ゲーム内でキャラクターを作成してください。サーバーでアカウントの自動作成が許可されている必要があります。",
        "게임 및 마켓 서비스를 계속 실행한 상태로 EVE 클라이언트를 실행합니다. 아래 계정 이름으로 로그인한 후 게임에서 캐릭터를 생성하세요. 서버에서 자동 계정 생성을 허용해야 합니다.",
        "Lance le client EVE en laissant les services de jeu et de marché actifs. Connectez-vous avec le nom de compte ci-dessous, puis créez votre personnage en jeu. Le serveur doit autoriser la création automatique des comptes.",
        "Startet den EVE-Client, während Spiel- und Marktdienste weiterlaufen. Melden Sie sich mit dem folgenden Kontonamen an und erstellen Sie Ihren Charakter im Spiel. Der Server muss die automatische Kontoerstellung erlauben.",
        "Start de EVE-client terwijl de game- en marktdiensten blijven draaien. Log in met de onderstaande accountnaam en maak daarna je personage in het spel. De server moet automatisch accounts aanmaken toestaan.",
        "Запускает клиент EVE, оставляя игровые и рыночные службы работающими. Войдите с указанным ниже именем учётной записи и создайте персонажа в игре. Сервер должен разрешать автоматическое создание учётных записей.",
    ),
    "Registration and account permissions are controlled by the server. "
    "Existing accounts can also log in here.": (
        "注册和账号权限由服务器控制。已有账号也可以在此登录。",
        "登録とアカウント権限はサーバーが管理します。既存のアカウントでもログインできます。",
        "등록 및 계정 권한은 서버에서 관리합니다. 기존 계정으로도 로그인할 수 있습니다.",
        "L'inscription et les droits du compte sont gérés par le serveur. Les comptes existants peuvent aussi se connecter ici.",
        "Registrierung und Kontoberechtigungen werden vom Server verwaltet. Bestehende Konten können sich hier ebenfalls anmelden.",
        "Registratie en accountrechten worden door de server beheerd. Je kunt hier ook met een bestaand account inloggen.",
        "Регистрацией и правами учётных записей управляет сервер. Здесь также можно войти в существующую учётную запись.",
    ),
    "LAUNCH CLIENT": (
        "启动客户端", "クライアントを起動", "클라이언트 실행", "LANCER LE CLIENT",
        "CLIENT STARTEN", "CLIENT STARTEN", "ЗАПУСТИТЬ КЛИЕНТ",
    ),
    "Copy overview once": ("仅复制一次总览", "オーバービューを一度だけコピー", "오버뷰 한 번 복사", "Copier la vue d’ensemble une fois", "Übersicht einmal kopieren", "Overzicht eenmalig kopiëren", "Скопировать обзор один раз"),
    "Close EVE clients before copying their overviews.": ("复制总览前请关闭 EVE 客户端。", "コピーする前に EVE クライアントを閉じてください。", "복사하기 전에 EVE 클라이언트를 닫으세요.", "Fermez les clients EVE avant de copier les vues d’ensemble.", "Schließe die EVE-Clients vor dem Kopieren.", "Sluit de EVE-clients voordat je de overzichten kopieert.", "Закройте клиенты EVE перед копированием обзоров."),
    "Save the Separate settings option first so the recipients can keep their own changes.": ("请先保存独立设置模式，以便目标角色保留自己的更改。", "コピー先が変更を保持できるよう、先に個別設定を保存してください。", "대상 캐릭터가 변경 사항을 유지할 수 있도록 개별 설정을 먼저 저장하세요.", "Enregistrez d’abord les réglages séparés pour que chaque destinataire conserve ses modifications.", "Speichere zuerst getrennte Einstellungen, damit die Zielcharaktere ihre Änderungen behalten.", "Sla eerst Afzonderlijke instellingen op zodat de doelpersonages hun eigen wijzigingen behouden.", "Сначала сохраните раздельные настройки, чтобы получатели могли сохранять свои изменения."),
    "Launch the source character once to capture its overview, then close EVE.": ("请启动源角色一次以捕获总览，然后关闭 EVE。", "コピー元のキャラクターで一度ログインしてから EVE を閉じてください。", "원본 캐릭터를 한 번 실행해 오버뷰를 저장한 뒤 EVE를 닫으세요.", "Lancez une fois le personnage source pour capturer sa vue d’ensemble, puis fermez EVE.", "Starte den Quellcharakter einmal zum Erfassen der Übersicht und schließe dann EVE.", "Start het bronpersonage één keer om het overzicht vast te leggen en sluit daarna EVE.", "Один раз запустите исходного персонажа для сохранения обзора, затем закройте EVE."),
    "Enable the Overview Bridge in Tools to copy an overview.": ("在工具中启用总览桥接功能后即可复制。", "ツールでオーバービューブリッジを有効にしてください。", "도구에서 오버뷰 브리지를 활성화하세요.", "Activez le pont de vue d’ensemble dans Outils.", "Aktiviere die Übersichtsbrücke unter Werkzeuge.", "Schakel de overzichtsbridge in bij Hulpmiddelen.", "Включите мост обзора в разделе «Инструменты»."),
    "Overview copy queued. It will apply once when each selected character next logs in.": ("总览复制已排队，将在各所选角色下次登录时应用一次。", "コピーを予約しました。選んだキャラクターの次回ログイン時に一度だけ適用されます。", "복사를 예약했습니다. 선택한 캐릭터가 다음에 로그인할 때 한 번 적용됩니다.", "Copie mise en attente. Elle s’applique une fois à la prochaine connexion de chaque personnage sélectionné.", "Die Übersicht wird bei der nächsten Anmeldung jedes ausgewählten Charakters einmal übernommen.", "De kopie staat klaar en wordt één keer toegepast wanneer elk gekozen personage weer inlogt.", "Копирование запланировано. Обзор применится один раз при следующем входе каждого выбранного персонажа."),
    "Choose a source character and the characters that should receive its overview.": ("选择源角色和要接收其总览的角色。", "コピー元とコピー先のキャラクターを選んでください。", "원본 캐릭터와 오버뷰를 받을 캐릭터를 선택하세요.", "Choisissez le personnage source et ceux qui recevront sa vue d’ensemble.", "Wähle den Quellcharakter und die Charaktere, die seine Übersicht erhalten sollen.", "Kies een bronpersonage en de personages die het overzicht moeten ontvangen.", "Выберите исходного персонажа и тех, кто получит его обзор."),
    "Overview copy is available for Native installations.": ("总览复制适用于原生安装。", "オーバービューのコピーはネイティブ環境で使用できます。", "오버뷰 복사는 네이티브 설치에서 사용할 수 있습니다.", "La copie de vue d’ensemble est disponible pour les installations natives.", "Übersichten lassen sich bei nativen Installationen kopieren.", "Overzichten kopiëren is beschikbaar voor native installaties.", "Копирование обзоров доступно для нативных установок."),
    "The selected installation or characters changed. Reopen Copy overview once.": (
        "所选安装或角色已更改。请重新打开仅复制一次总览。", "選択したインストールまたはキャラクターが変わりました。コピー画面を開き直してください。",
        "선택한 설치 또는 캐릭터가 변경되었습니다. 오버뷰 복사 창을 다시 여세요.", "L’installation ou les personnages sélectionnés ont changé. Rouvrez la copie de vue d’ensemble.",
        "Die ausgewählte Installation oder die Charaktere wurden geändert. Öffne die Übersichtskopie erneut.", "De gekozen installatie of personages zijn gewijzigd. Open de overzichtskopie opnieuw.",
        "Выбранная установка или персонажи изменились. Откройте окно копирования обзора заново.",
    ),
    "One of the selected characters already has an overview copy queued.": (
        "其中一个所选角色已排队等待复制总览。", "選択したキャラクターの一人にコピーがすでに予約されています。", "선택한 캐릭터 중 하나에 이미 오버뷰 복사가 예약되어 있습니다.",
        "Une copie de vue d’ensemble est déjà en attente pour l’un des personnages sélectionnés.", "Für einen ausgewählten Charakter ist bereits eine Übersichtskopie vorgemerkt.",
        "Voor een van de gekozen personages staat al een overzichtskopie klaar.", "Для одного из выбранных персонажей уже запланировано копирование обзора.",
    ),
}

SOURCE_PHRASES = tuple(_ROWS)
UI_PHRASES_BY_LANGUAGE = {
    language: {source: values[index] for source, values in _ROWS.items()}
    for index, language in enumerate(_LANGUAGES)
}
