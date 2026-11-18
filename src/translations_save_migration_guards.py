"""Stock EveJS Save Migration storage verification phrases."""

SOURCE_PHRASES = (
    "The Docker image save format could not be verified against this installation.",
    "The Docker image save format differs from this installation. Set up matching images first.",
    "Game still owns this save. Stop it and let its last write lease expire.",
    "The game save has unfinished writes. Start and cleanly stop Game before retrying.",
)

_VALUES = {
    "nl": (
        "Kon niet controleren of het opslagformaat van de Docker-image overeenkomt met deze installatie.",
        "Het opslagformaat van de Docker-image wijkt af van dat van deze installatie. Stel eerst passende images in.",
        "De gameserver heeft nog schrijftoegang tot deze spelstand. Stop de server en wacht tot de schrijftoegang verloopt.",
        "Er staan nog schrijfbewerkingen open voor deze spelstand. Start de gameserver, sluit hem normaal af en probeer het opnieuw.",
    ),
    "de": (
        "Das Spielstandformat des Docker-Images konnte nicht mit dem dieser Installation abgeglichen werden.",
        "Das Spielstandformat des Docker-Images weicht von dem dieser Installation ab. Richte zuerst passende Images ein.",
        "Der Spielserver hat noch Schreibzugriff auf diesen Spielstand. Stoppe den Server und warte, bis die Schreibberechtigung abläuft.",
        "Für den Spielstand sind noch Schreibvorgänge offen. Starte den Spielserver und fahre ihn ordnungsgemäß herunter, bevor du es erneut versuchst.",
    ),
    "fr": (
        "Impossible de vérifier que le format de sauvegarde de l’image Docker correspond à cette installation.",
        "Le format de sauvegarde de l’image Docker diffère de celui de cette installation. Configurez d’abord des images compatibles.",
        "Le serveur de jeu dispose encore d’un accès en écriture à cette sauvegarde. Arrêtez-le et attendez que cet accès expire.",
        "Des écritures restent en attente dans la sauvegarde. Démarrez le serveur de jeu, arrêtez-le normalement, puis réessayez.",
    ),
    "ru": (
        "Не удалось проверить, соответствует ли формат сохранений образа Docker этой установке.",
        "Формат сохранений образа Docker отличается от формата этой установки. Сначала настройте подходящие образы.",
        "У игрового сервера ещё есть право записи в это сохранение. Остановите сервер и дождитесь, пока право записи истечёт.",
        "В сохранении есть незавершённые операции записи. Запустите игровой сервер, штатно остановите его и повторите попытку.",
    ),
    "zh_CN": (
        "无法验证 Docker 镜像的存档格式是否与此安装实例一致。",
        "Docker 镜像的存档格式与此安装实例不同。请先配置与该安装实例匹配的镜像。",
        "游戏服务器仍持有该存档的写入租约。请停止游戏服务器，并等待当前写入租约到期。",
        "游戏存档中有未完成的写入。请启动游戏服务器，正常关闭后再重试。",
    ),
    "ja": (
        "Dockerイメージのセーブ形式がこのインストールと一致するか確認できませんでした。",
        "Dockerイメージとこのインストールのセーブ形式が異なります。先に一致するイメージをセットアップしてください。",
        "ゲームサーバーがまだこのセーブデータの書き込み権限を保持しています。サーバーを停止し、その権限が失効するまで待ってください。",
        "セーブデータへの書き込みが完了していません。ゲームサーバーを起動して正常に停止してから、もう一度お試しください。",
    ),
    "ko": (
        "Docker 이미지의 세이브 형식이 이 설치 환경과 일치하는지 확인할 수 없습니다.",
        "Docker 이미지의 세이브 형식이 이 설치 환경의 형식과 다릅니다. 먼저 일치하는 이미지를 설정하세요.",
        "게임 서버가 아직 이 세이브의 쓰기 권한을 갖고 있습니다. 서버를 중지하고 해당 권한이 만료될 때까지 기다리세요.",
        "세이브에 완료되지 않은 쓰기 작업이 있습니다. 게임 서버를 시작한 뒤 정상적으로 중지하고 다시 시도하세요.",
    ),
}

UI_PHRASES_BY_LANGUAGE = {
    language: dict(zip(SOURCE_PHRASES, values, strict=True))
    for language, values in _VALUES.items()
}
