"""Small, dependency-free localization catalog for the launcher shell."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re

from .translations_eu import UI_PHRASES_BY_LANGUAGE as EU_UI_PHRASES
from .translations_ja_ko import UI_PHRASES_BY_LANGUAGE as JA_KO_UI_PHRASES
from .translations_ru import UI_PHRASES as RU_UI_PHRASES
from .translations_source import SOURCE_PHRASES, SOURCE_PHRASE_SET
from .translations_zh_cn import UI_PHRASES as ZH_CN_UI_PHRASES


UI_PHRASES_BY_LANGUAGE: dict[str, dict[str, str]] = {
    "zh_CN": ZH_CN_UI_PHRASES,
    **JA_KO_UI_PHRASES,
    **EU_UI_PHRASES,
    "ru": RU_UI_PHRASES,
}


DEFAULT_LANGUAGE = "en"


@dataclass(frozen=True)
class LanguageOption:
    code: str
    flag: str
    native_name: str

    @property
    def display_name(self) -> str:
        """Return the stable native label shown in every language selector."""
        return self.native_name

    @property
    def english_name(self) -> str:
        """Compatibility alias retained for older selector callers."""
        return self.native_name

    @property
    def label(self) -> str:
        return f"{self.flag}  {self.display_name}"


LANGUAGES: tuple[LanguageOption, ...] = (
    LanguageOption("en", "🇬🇧", "English"),
    LanguageOption("zh_CN", "🇨🇳", "简体中文"),
    LanguageOption("ja", "🇯🇵", "日本語"),
    LanguageOption("ko", "🇰🇷", "한국어"),
    LanguageOption("fr", "🇫🇷", "Français"),
    LanguageOption("de", "🇩🇪", "Deutsch"),
    LanguageOption("nl", "🇳🇱", "Nederlands"),
    LanguageOption("ru", "🇷🇺", "Русский"),
)

_LANGUAGE_CODES = {option.code for option in LANGUAGES}
_current_language = DEFAULT_LANGUAGE


_ENGLISH = {
    "nav.accessible_name": "Primary launcher navigation",
    "nav.command_deck": "COMMAND DECK",
    "nav.home": "Home",
    "nav.characters": "Characters",
    "nav.mods": "Mods",
    "nav.tools": "Tools",
    "nav.settings": "Settings",
    "nav.system_control": "SYSTEM CONTROL",
    "nav.server": "Server",
    "nav.market": "Market",
    "nav.kill_all": "Kill All Clients",
    "nav.language_tooltip": "Launcher language",
    "tooltip.kill_all_active": "Terminate every running EVE client",
    "tooltip.kill_all_inactive": "No EVE clients are running",
    "service.start": "▶ Start {service}",
    "service.starting": "⏳ Starting {service}…",
    "service.stop": "■ Stop {service}",
    "service.stopping": "⏳ Stopping {service}…",
    "service.retry": "↻ Retry {service}",
    "service.state_starting": "{service}: Starting...",
    "service.state_stopping": "{service}: Stopping...",
    "service.unknown": "{service}: Unknown",
    "service.docker_unavailable": "{service}: Docker unavailable",
    "service.offline": "{service}: Offline",
    "service.online": "{service}: Online",
    "service.failed": "{service}: Failed",
    "service.external": "{service}: External",
    "tooltip.docker_connect_only": "Connect-only Docker mode cannot change containers.",
    "tooltip.docker_unavailable": "Docker state is unavailable",
    "tooltip.service_changing": "{service} is changing state",
    "tooltip.service_external": (
        "{service} was started outside this launcher and must be stopped "
        "from its original console"
    ),
    "tooltip.service_starting": "{service} is starting",
    "tooltip.service_stopping": "{service} is stopping",
    "tooltip.stop_server_first": "Stop Server first",
}

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": _ENGLISH,
    "zh_CN": {
        "nav.accessible_name": "启动器主导航",
        "nav.command_deck": "指挥台",
        "nav.home": "首页",
        "nav.characters": "角色",
        "nav.mods": "模组",
        "nav.tools": "工具",
        "nav.settings": "设置",
        "nav.system_control": "系统控制",
        "nav.server": "游戏服务",
        "nav.market": "市场服务",
        "nav.kill_all": "关闭所有客户端",
        "nav.language_tooltip": "启动器语言",
        "tooltip.kill_all_active": "关闭所有正在运行的 EVE 客户端",
        "tooltip.kill_all_inactive": "当前没有运行中的 EVE 客户端",
        "service.start": "▶ 启动{service}",
        "service.starting": "⏳ 正在启动{service}…",
        "service.stop": "■ 停止{service}",
        "service.stopping": "⏳ 正在停止{service}…",
        "service.retry": "↻ 重试{service}",
        "service.state_starting": "{service}：正在启动…",
        "service.state_stopping": "{service}：正在停止…",
        "service.unknown": "{service}：未知",
        "service.docker_unavailable": "{service}：Docker 不可用",
        "service.offline": "{service}：离线",
        "service.online": "{service}：在线",
        "service.failed": "{service}：失败",
        "service.external": "{service}：外部运行",
        "tooltip.docker_connect_only": "仅连接 Docker 模式无法更改容器。",
        "tooltip.docker_unavailable": "Docker 状态不可用",
        "tooltip.service_changing": "{service}正在更改状态",
        "tooltip.service_external": "{service}由启动器外部启动，必须在原控制台中停止",
        "tooltip.service_starting": "{service}正在启动",
        "tooltip.service_stopping": "{service}正在停止",
        "tooltip.stop_server_first": "请先停止游戏服务",
    },
    "ja": {
        "nav.accessible_name": "ランチャーのメインナビゲーション",
        "nav.command_deck": "コマンドデッキ",
        "nav.home": "ホーム",
        "nav.characters": "キャラクター",
        "nav.mods": "モッド",
        "nav.tools": "ツール",
        "nav.settings": "設定",
        "nav.system_control": "システム制御",
        "nav.server": "ゲームサーバー",
        "nav.market": "マーケットサーバー",
        "nav.kill_all": "全クライアントを終了",
        "nav.language_tooltip": "ランチャーの言語",
        "tooltip.kill_all_active": "実行中の EVE クライアントをすべて終了",
        "tooltip.kill_all_inactive": "実行中の EVE クライアントはありません",
        "service.start": "▶ {service}を起動",
        "service.starting": "⏳ {service}を起動中…",
        "service.stop": "■ {service}を停止",
        "service.stopping": "⏳ {service}を停止中…",
        "service.retry": "↻ {service}を再試行",
        "service.state_starting": "{service}：起動中…",
        "service.state_stopping": "{service}：停止中…",
        "service.unknown": "{service}：不明",
        "service.docker_unavailable": "{service}：Docker 利用不可",
        "service.offline": "{service}：オフライン",
        "service.online": "{service}：オンライン",
        "service.failed": "{service}：失敗",
        "service.external": "{service}：外部実行",
        "tooltip.docker_connect_only": "接続専用 Docker モードではコンテナを変更できません。",
        "tooltip.docker_unavailable": "Docker の状態を取得できません",
        "tooltip.service_changing": "{service}の状態を変更中です",
        "tooltip.service_external": "{service}はランチャー外で起動されたため、元のコンソールから停止してください",
        "tooltip.service_starting": "{service}を起動中です",
        "tooltip.service_stopping": "{service}を停止中です",
        "tooltip.stop_server_first": "先にサーバーを停止してください",
    },
    "ko": {
        "nav.accessible_name": "런처 기본 탐색",
        "nav.command_deck": "명령 콘솔",
        "nav.home": "홈",
        "nav.characters": "캐릭터",
        "nav.mods": "모드",
        "nav.tools": "도구",
        "nav.settings": "설정",
        "nav.system_control": "시스템 제어",
        "nav.server": "서버",
        "nav.market": "마켓",
        "nav.kill_all": "모든 클라이언트 종료",
        "nav.language_tooltip": "런처 언어",
        "tooltip.kill_all_active": "실행 중인 모든 EVE 클라이언트 종료",
        "tooltip.kill_all_inactive": "실행 중인 EVE 클라이언트가 없습니다",
        "service.start": "▶ {service} 시작",
        "service.starting": "⏳ {service} 시작 중…",
        "service.stop": "■ {service} 중지",
        "service.stopping": "⏳ {service} 중지 중…",
        "service.retry": "↻ {service} 다시 시도",
        "service.state_starting": "{service}: 시작 중…",
        "service.state_stopping": "{service}: 중지 중…",
        "service.unknown": "{service}: 알 수 없음",
        "service.docker_unavailable": "{service}: Docker 사용 불가",
        "service.offline": "{service}: 오프라인",
        "service.online": "{service}: 온라인",
        "service.failed": "{service}: 실패",
        "service.external": "{service}: 외부 실행",
        "tooltip.docker_connect_only": "연결 전용 Docker 모드에서는 컨테이너를 변경할 수 없습니다.",
        "tooltip.docker_unavailable": "Docker 상태를 확인할 수 없습니다",
        "tooltip.service_changing": "{service} 상태 변경 중",
        "tooltip.service_external": "{service}가 런처 외부에서 시작되어 원래 콘솔에서 중지해야 합니다",
        "tooltip.service_starting": "{service} 시작 중",
        "tooltip.service_stopping": "{service} 중지 중",
        "tooltip.stop_server_first": "먼저 서버를 중지하세요",
    },
    "fr": {
        "nav.accessible_name": "Navigation principale du lanceur",
        "nav.command_deck": "PONT DE COMMANDE",
        "nav.home": "Accueil",
        "nav.characters": "Personnages",
        "nav.mods": "Modifications",
        "nav.tools": "Outils",
        "nav.settings": "Paramètres",
        "nav.system_control": "CONTRÔLE SYSTÈME",
        "nav.server": "Serveur",
        "nav.market": "Marché",
        "nav.kill_all": "Fermer tous les clients",
        "nav.language_tooltip": "Langue du lanceur",
        "tooltip.kill_all_active": "Fermer tous les clients EVE en cours",
        "tooltip.kill_all_inactive": "Aucun client EVE n’est en cours",
        "service.start": "▶ Démarrer {service}",
        "service.starting": "⏳ Démarrage de {service}…",
        "service.stop": "■ Arrêter {service}",
        "service.stopping": "⏳ Arrêt de {service}…",
        "service.retry": "↻ Réessayer {service}",
        "service.state_starting": "{service} : démarrage…",
        "service.state_stopping": "{service} : arrêt…",
        "service.unknown": "{service} : état inconnu",
        "service.docker_unavailable": "{service} : Docker indisponible",
        "service.offline": "{service} : hors ligne",
        "service.online": "{service} : en ligne",
        "service.failed": "{service} : échec",
        "service.external": "{service} : externe",
        "tooltip.docker_connect_only": "Le mode Docker en connexion seule ne peut pas modifier les conteneurs.",
        "tooltip.docker_unavailable": "État Docker indisponible",
        "tooltip.service_changing": "Changement d’état de {service}",
        "tooltip.service_external": "{service} a été démarré hors du lanceur et doit être arrêté depuis sa console d’origine",
        "tooltip.service_starting": "Démarrage de {service}",
        "tooltip.service_stopping": "Arrêt de {service}",
        "tooltip.stop_server_first": "Arrêtez d’abord le serveur",
    },
    "de": {
        "nav.accessible_name": "Hauptnavigation des Launchers",
        "nav.command_deck": "KOMMANDODECK",
        "nav.home": "Start",
        "nav.characters": "Charaktere",
        "nav.mods": "Modifikationen",
        "nav.tools": "Werkzeuge",
        "nav.settings": "Einstellungen",
        "nav.system_control": "SYSTEMSTEUERUNG",
        "nav.server": "Server",
        "nav.market": "Markt",
        "nav.kill_all": "Alle Clients beenden",
        "nav.language_tooltip": "Launcher-Sprache",
        "tooltip.kill_all_active": "Alle laufenden EVE-Clients beenden",
        "tooltip.kill_all_inactive": "Keine EVE-Clients laufen",
        "service.start": "▶ {service} starten",
        "service.starting": "⏳ {service} wird gestartet…",
        "service.stop": "■ {service} stoppen",
        "service.stopping": "⏳ {service} wird gestoppt…",
        "service.retry": "↻ {service} erneut versuchen",
        "service.state_starting": "{service}: wird gestartet…",
        "service.state_stopping": "{service}: wird gestoppt…",
        "service.unknown": "{service}: unbekannt",
        "service.docker_unavailable": "{service}: Docker nicht verfügbar",
        "service.offline": "{service}: offline",
        "service.online": "{service}: online",
        "service.failed": "{service}: fehlgeschlagen",
        "service.external": "{service}: extern",
        "tooltip.docker_connect_only": "Im reinen Docker-Verbindungsmodus können Container nicht geändert werden.",
        "tooltip.docker_unavailable": "Docker-Status ist nicht verfügbar",
        "tooltip.service_changing": "{service} ändert gerade den Status",
        "tooltip.service_external": "{service} wurde außerhalb des Launchers gestartet und muss in der ursprünglichen Konsole gestoppt werden",
        "tooltip.service_starting": "{service} wird gestartet",
        "tooltip.service_stopping": "{service} wird gestoppt",
        "tooltip.stop_server_first": "Zuerst den Server stoppen",
    },
    "nl": {
        "nav.accessible_name": "Hoofdnavigatie van de launcher",
        "nav.command_deck": "COMMANDODEK",
        "nav.home": "Start",
        "nav.characters": "Personages",
        "nav.mods": "Aanpassingen",
        "nav.tools": "Hulpmiddelen",
        "nav.settings": "Instellingen",
        "nav.system_control": "SYSTEEMBEDIENING",
        "nav.server": "Server",
        "nav.market": "Markt",
        "nav.kill_all": "Alle clients afsluiten",
        "nav.language_tooltip": "Taal van de launcher",
        "tooltip.kill_all_active": "Alle actieve EVE-clients afsluiten",
        "tooltip.kill_all_inactive": "Er zijn geen actieve EVE-clients",
        "service.start": "▶ {service} starten",
        "service.starting": "⏳ {service} wordt gestart…",
        "service.stop": "■ {service} stoppen",
        "service.stopping": "⏳ {service} wordt gestopt…",
        "service.retry": "↻ {service} opnieuw proberen",
        "service.state_starting": "{service}: wordt gestart…",
        "service.state_stopping": "{service}: wordt gestopt…",
        "service.unknown": "{service}: onbekend",
        "service.docker_unavailable": "{service}: Docker niet beschikbaar",
        "service.offline": "{service}: offline",
        "service.online": "{service}: online",
        "service.failed": "{service}: mislukt",
        "service.external": "{service}: extern",
        "tooltip.docker_connect_only": "Docker in alleen-verbindenmodus kan containers niet wijzigen.",
        "tooltip.docker_unavailable": "Docker-status is niet beschikbaar",
        "tooltip.service_changing": "De status van {service} wordt gewijzigd",
        "tooltip.service_external": "{service} is buiten de launcher gestart en moet via de oorspronkelijke console worden gestopt",
        "tooltip.service_starting": "{service} wordt gestart",
        "tooltip.service_stopping": "{service} wordt gestopt",
        "tooltip.stop_server_first": "Stop eerst de server",
    },
    "ru": {
        "nav.accessible_name": "Основная навигация лаунчера",
        "nav.command_deck": "ПАНЕЛЬ УПРАВЛЕНИЯ",
        "nav.home": "Главная",
        "nav.characters": "Персонажи",
        "nav.mods": "Моды",
        "nav.tools": "Инструменты",
        "nav.settings": "Настройки",
        "nav.system_control": "УПРАВЛЕНИЕ СИСТЕМОЙ",
        "nav.server": "Сервер",
        "nav.market": "Рынок",
        "nav.kill_all": "Закрыть все клиенты",
        "nav.language_tooltip": "Язык лаунчера",
        "tooltip.kill_all_active": "Завершить работу всех запущенных клиентов EVE",
        "tooltip.kill_all_inactive": "Нет запущенных клиентов EVE",
        "service.start": "▶ Запустить {service}",
        "service.starting": "⏳ Запуск: {service}…",
        "service.stop": "■ Остановить {service}",
        "service.stopping": "⏳ Остановка: {service}…",
        "service.retry": "↻ Повторить запуск: {service}",
        "service.state_starting": "{service}: запуск…",
        "service.state_stopping": "{service}: остановка…",
        "service.unknown": "{service}: состояние неизвестно",
        "service.docker_unavailable": "{service}: Docker недоступен",
        "service.offline": "{service}: не в сети",
        "service.online": "{service}: в сети",
        "service.failed": "{service}: ошибка",
        "service.external": "{service}: запущен вне лаунчера",
        "tooltip.docker_connect_only": "Режим Docker «только подключение» не позволяет изменять контейнеры.",
        "tooltip.docker_unavailable": "Состояние Docker недоступно",
        "tooltip.service_changing": "Состояние службы «{service}» изменяется",
        "tooltip.service_external": "Служба «{service}» запущена вне лаунчера; остановите её в исходной консоли",
        "tooltip.service_starting": "Запуск службы «{service}»",
        "tooltip.service_stopping": "Остановка службы «{service}»",
        "tooltip.stop_server_first": "Сначала остановите сервер",
    },
}


# Character/account deletion confirmations contain both launcher-owned grammar
# and user values.  Keep the complete sentence structure in the keyed catalog
# instead of passing English fragments through the generic template matcher,
# which deliberately preserves every inserted value verbatim.
_CHARACTER_DELETION_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "deletion.confirm_account": (
            "Delete account '{username}' and {count} character(s)?\n\n"
            "Characters: {names}\n\n"
            "EveJS will run its native character cleanup. The launcher will keep "
            "a recoverable backup of every affected table and portrait. Account "
            "profile/settings folders are preserved.{service_note}"
        ),
        "deletion.confirm_character": (
            "Delete character '{character_name}'?\n\n"
            "Account '{username}' will be retained.\n\n"
            "EveJS will run its native character cleanup. The launcher will keep "
            "a recoverable backup of every affected table and portrait. Account "
            "profile/settings folders are preserved.{service_note}"
        ),
        "deletion.service_note": (
            "\n\nLauncher-owned EveJS services will be stopped and restored."
        ),
    },
    "zh_CN": {
        "deletion.confirm_account": (
            "删除账户“{username}”及其 {count} 个角色？\n\n"
            "角色：{names}\n\n"
            "EveJS 将执行原生角色清理。启动器会为所有受影响的数据表和肖像保留"
            "可恢复备份。账户配置档/设置文件夹将被保留。{service_note}"
        ),
        "deletion.confirm_character": (
            "删除角色“{character_name}”？\n\n"
            "账户“{username}”将被保留。\n\n"
            "EveJS 将执行原生角色清理。启动器会为所有受影响的数据表和肖像保留"
            "可恢复备份。账户配置档/设置文件夹将被保留。{service_note}"
        ),
        "deletion.service_note": (
            "\n\n启动器管理的 EveJS 服务将停止并恢复。"
        ),
    },
    "ja": {
        "deletion.confirm_account": (
            "アカウント「{username}」とそのキャラクター {count} 人を削除しますか？\n\n"
            "キャラクター：{names}\n\n"
            "EveJS の標準キャラクター削除処理を実行します。ランチャーは影響を受ける"
            "すべてのテーブルとポートレートについて、復元可能なバックアップを保持します。"
            "アカウントのプロファイル/設定フォルダーは保持されます。{service_note}"
        ),
        "deletion.confirm_character": (
            "キャラクター「{character_name}」を削除しますか？\n\n"
            "アカウント「{username}」は保持されます。\n\n"
            "EveJS の標準キャラクター削除処理を実行します。ランチャーは影響を受ける"
            "すべてのテーブルとポートレートについて、復元可能なバックアップを保持します。"
            "アカウントのプロファイル/設定フォルダーは保持されます。{service_note}"
        ),
        "deletion.service_note": (
            "\n\nランチャーが管理する EveJS サービスは停止後に復元されます。"
        ),
    },
    "ko": {
        "deletion.confirm_account": (
            "계정 '{username}' 및 캐릭터 {count}명을 삭제할까요?\n\n"
            "캐릭터: {names}\n\n"
            "EveJS에서 기본 캐릭터 정리를 실행합니다. 런처는 영향을 받는 모든 테이블과 "
            "초상화의 복구 가능한 백업을 보관합니다. 계정 프로필/설정 폴더는 "
            "유지됩니다.{service_note}"
        ),
        "deletion.confirm_character": (
            "캐릭터 '{character_name}'을(를) 삭제할까요?\n\n"
            "계정 '{username}'은(는) 유지됩니다.\n\n"
            "EveJS에서 기본 캐릭터 정리를 실행합니다. 런처는 영향을 받는 모든 테이블과 "
            "초상화의 복구 가능한 백업을 보관합니다. 계정 프로필/설정 폴더는 "
            "유지됩니다.{service_note}"
        ),
        "deletion.service_note": (
            "\n\n런처에서 관리하는 EveJS 서비스를 중지한 뒤 복원합니다."
        ),
    },
    "fr": {
        "deletion.confirm_account": (
            "Supprimer le compte « {username} » et ses {count} personnage(s) ?\n\n"
            "Personnages : {names}\n\n"
            "EveJS exécutera son nettoyage natif des personnages. Le lanceur conservera "
            "une sauvegarde récupérable de chaque table et portrait affecté. Les dossiers "
            "de profil et de paramètres du compte seront conservés.{service_note}"
        ),
        "deletion.confirm_character": (
            "Supprimer le personnage « {character_name} » ?\n\n"
            "Le compte « {username} » sera conservé.\n\n"
            "EveJS exécutera son nettoyage natif des personnages. Le lanceur conservera "
            "une sauvegarde récupérable de chaque table et portrait affecté. Les dossiers "
            "de profil et de paramètres du compte seront conservés.{service_note}"
        ),
        "deletion.service_note": (
            "\n\nLes services EveJS gérés par le lanceur seront arrêtés puis restaurés."
        ),
    },
    "de": {
        "deletion.confirm_account": (
            "Konto „{username}“ und {count} Charakter(e) löschen?\n\n"
            "Charaktere: {names}\n\n"
            "Die native EveJS-Charakterbereinigung wird ausgeführt. Der Launcher behält "
            "eine wiederherstellbare Sicherung jeder betroffenen Tabelle und jedes Porträts. "
            "Die Profil- und Einstellungsordner des Kontos bleiben erhalten.{service_note}"
        ),
        "deletion.confirm_character": (
            "Charakter „{character_name}“ löschen?\n\n"
            "Konto „{username}“ bleibt erhalten.\n\n"
            "Die native EveJS-Charakterbereinigung wird ausgeführt. Der Launcher behält "
            "eine wiederherstellbare Sicherung jeder betroffenen Tabelle und jedes Porträts. "
            "Die Profil- und Einstellungsordner des Kontos bleiben erhalten.{service_note}"
        ),
        "deletion.service_note": (
            "\n\nVom Launcher verwaltete EveJS-Dienste werden gestoppt und wiederhergestellt."
        ),
    },
    "nl": {
        "deletion.confirm_account": (
            "Account ‘{username}’ en {count} personage(s) verwijderen?\n\n"
            "Personages: {names}\n\n"
            "EveJS voert de eigen personageopschoning uit. De launcher bewaart een "
            "herstelbare back-up van elke betrokken tabel en elk portret. De profiel- en "
            "instellingenmappen van het account blijven behouden.{service_note}"
        ),
        "deletion.confirm_character": (
            "Personage ‘{character_name}’ verwijderen?\n\n"
            "Account ‘{username}’ blijft behouden.\n\n"
            "EveJS voert de eigen personageopschoning uit. De launcher bewaart een "
            "herstelbare back-up van elke betrokken tabel en elk portret. De profiel- en "
            "instellingenmappen van het account blijven behouden.{service_note}"
        ),
        "deletion.service_note": (
            "\n\nDoor de launcher beheerde EveJS-services worden gestopt en hersteld."
        ),
    },
    "ru": {
        "deletion.confirm_account": (
            "Удалить учётную запись «{username}» и {count} персонаж(ей)?\n\n"
            "Персонажи: {names}\n\n"
            "EveJS выполнит штатную очистку персонажей. Лаунчер сохранит восстанавливаемую "
            "резервную копию каждой затронутой таблицы и портрета. Папки профиля и настроек "
            "учётной записи будут сохранены.{service_note}"
        ),
        "deletion.confirm_character": (
            "Удалить персонажа «{character_name}»?\n\n"
            "Учётная запись «{username}» будет сохранена.\n\n"
            "EveJS выполнит штатную очистку персонажей. Лаунчер сохранит восстанавливаемую "
            "резервную копию каждой затронутой таблицы и портрета. Папки профиля и настроек "
            "учётной записи будут сохранены.{service_note}"
        ),
        "deletion.service_note": (
            "\n\nСлужбы EveJS под управлением лаунчера будут остановлены и восстановлены."
        ),
    },
}

for _language, _catalog in _TRANSLATIONS.items():
    _catalog.update(_CHARACTER_DELETION_TRANSLATIONS[_language])


def normalize_language(value: object) -> str:
    if not isinstance(value, str):
        return DEFAULT_LANGUAGE
    normalized = value.strip().replace("-", "_")
    aliases = {
        "zh": "zh_CN",
        "zh_Hans": "zh_CN",
        "zh_cn": "zh_CN",
        "ru_RU": "ru",
        "ru_ru": "ru",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _LANGUAGE_CODES else DEFAULT_LANGUAGE


def language_for_system_locale(value: object) -> str:
    """Map a Windows/Qt locale to a supported UI language or English."""
    if not isinstance(value, str) or not value.strip():
        return DEFAULT_LANGUAGE
    normalized = value.strip().replace("-", "_")
    language = normalized.split("_", 1)[0].casefold()
    if language == "zh":
        return "zh_CN"
    supported = {
        "en": "en",
        "ja": "ja",
        "ko": "ko",
        "fr": "fr",
        "de": "de",
        "nl": "nl",
        "ru": "ru",
    }
    return supported.get(language, DEFAULT_LANGUAGE)


def language_for_startup(
    *,
    has_saved_config: bool,
    saved_language: object,
    system_locale: object,
) -> str:
    """Choose a first-run locale without overriding an existing preference."""
    if has_saved_config:
        return normalize_language(saved_language)
    return language_for_system_locale(system_locale)


def set_language(code: object) -> str:
    global _current_language
    _current_language = normalize_language(code)
    return _current_language


def current_language() -> str:
    return _current_language


def _translate_for_language(
    language: str,
    key: str,
    **values: object,
) -> str:
    template = _TRANSLATIONS.get(language, {}).get(
        key,
        _ENGLISH.get(key, key),
    )
    return template.format(**values) if values else template


def translate(key: str, **values: object) -> str:
    return _translate_for_language(_current_language, key, **values)


def format_character_deletion_confirmation(
    scope: str,
    *,
    username: str,
    character_name: str,
    character_names: str,
    character_count: int,
    services_owned: bool,
) -> str:
    """Localize deletion grammar while preserving the supplied user values."""

    if scope not in {"account", "character"}:
        raise ValueError(f"Unsupported character deletion scope: {scope!r}")
    service_note = translate("deletion.service_note") if services_owned else ""
    if scope == "account":
        return translate(
            "deletion.confirm_account",
            username=username,
            count=character_count,
            names=character_names,
            service_note=service_note,
        )
    return translate(
        "deletion.confirm_character",
        character_name=character_name,
        username=username,
        service_note=service_note,
    )


_DISCOVERY_DIAGNOSTIC_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "not_found": "Path does not exist: {value}",
        "not_folder": "Path is not a folder: {value}",
        "not_file": "Path is not a file: {value}",
        "absolute": "An absolute path is required.",
        "missing": "Required EveJS component is missing: {value}",
    },
    "zh_CN": {
        "not_found": "路径不存在：{value}",
        "not_folder": "路径不是文件夹：{value}",
        "not_file": "路径不是文件：{value}",
        "absolute": "必须使用绝对路径。",
        "missing": "缺少必需的 EveJS 组件：{value}",
    },
    "ja": {
        "not_found": "パスが存在しません：{value}",
        "not_folder": "パスはフォルダーではありません：{value}",
        "not_file": "パスはファイルではありません：{value}",
        "absolute": "絶対パスが必要です。",
        "missing": "必要な EveJS コンポーネントがありません：{value}",
    },
    "ko": {
        "not_found": "경로가 존재하지 않습니다: {value}",
        "not_folder": "경로가 폴더가 아닙니다: {value}",
        "not_file": "경로가 파일이 아닙니다: {value}",
        "absolute": "절대 경로가 필요합니다.",
        "missing": "필수 EveJS 구성 요소가 없습니다: {value}",
    },
    "fr": {
        "not_found": "Le chemin n’existe pas : {value}",
        "not_folder": "Le chemin n’est pas un dossier : {value}",
        "not_file": "Le chemin n’est pas un fichier : {value}",
        "absolute": "Un chemin absolu est requis.",
        "missing": "Un composant EveJS requis est manquant : {value}",
    },
    "de": {
        "not_found": "Der Pfad existiert nicht: {value}",
        "not_folder": "Der Pfad ist kein Ordner: {value}",
        "not_file": "Der Pfad ist keine Datei: {value}",
        "absolute": "Ein absoluter Pfad ist erforderlich.",
        "missing": "Eine erforderliche EveJS-Komponente fehlt: {value}",
    },
    "nl": {
        "not_found": "Het pad bestaat niet: {value}",
        "not_folder": "Het pad is geen map: {value}",
        "not_file": "Het pad is geen bestand: {value}",
        "absolute": "Een absoluut pad is vereist.",
        "missing": "Een vereist EveJS-onderdeel ontbreekt: {value}",
    },
    "ru": {
        "not_found": "Путь не существует: {value}",
        "not_folder": "Путь не является папкой: {value}",
        "not_file": "Путь не является файлом: {value}",
        "absolute": "Требуется абсолютный путь.",
        "missing": "Отсутствует обязательный компонент EveJS: {value}",
    },
}


def translate_discovery_diagnostic(
    diagnostic: str,
    language: object | None = None,
) -> str:
    """Translate stable path validation while preserving paths and unknown errors."""

    code = current_language() if language is None else normalize_language(language)
    key: str | None = None
    value = ""
    prefixes = (
        ("Path does not exist: ", "not_found"),
        ("Path is not a directory: ", "not_folder"),
        ("Docker project root does not exist: ", "not_found"),
        ("Docker project root is not a directory: ", "not_folder"),
        ("Docker Compose file does not exist: ", "not_found"),
        ("Docker Compose path is not a file: ", "not_file"),
        ("Missing SSL cert (server may not be configured): ", "missing"),
        ("Missing Client config script: ", "missing"),
    )
    for prefix, candidate in prefixes:
        if diagnostic.startswith(prefix):
            key = candidate
            value = diagnostic[len(prefix) :]
            break
    if diagnostic in {
        "Docker project root must be an absolute path.",
        "Docker Compose file must be an absolute path.",
    }:
        key = "absolute"
    elif diagnostic == "Missing server start script (StartServer*.bat) or server/index.js":
        key = "missing"
        value = "StartServer*.bat / server/index.js"
    elif diagnostic.startswith("Missing game store: expected "):
        key = "missing"
        value = (
            "_local/gameStore/gamestore.sqlite / "
            "_local/gameStore/manifest.json / _local/gameStore/data"
        )
    if key is None:
        return diagnostic
    template = _DISCOVERY_DIAGNOSTIC_TRANSLATIONS[code][key]
    return template.format(value=value)


_SHELL_SOURCE_KEYS = {
    "Home": "nav.home",
    "Characters": "nav.characters",
    "Mods": "nav.mods",
    "Tools": "nav.tools",
    "Settings": "nav.settings",
    "Server": "nav.server",
    "Market": "nav.market",
    "Kill All Clients": "nav.kill_all",
    "COMMAND DECK": "nav.command_deck",
    "SYSTEM CONTROL": "nav.system_control",
}


def translate_ui_phrase(
    text: str,
    language: object | None = None,
    *,
    allow_templates: bool = False,
    template_min_literal: int = 0,
) -> str:
    """Translate a reviewed static UI phrase while preserving unknown text."""
    if not text:
        return text
    code = _current_language if language is None else normalize_language(language)
    if "\n" in text:
        lines = text.split("\n")
        if all(
            not line
            or is_reviewed_ui_phrase(line, allow_templates=allow_templates)
            for line in lines
        ):
            return "\n".join(
                translate_ui_phrase(
                    line,
                    code,
                    allow_templates=allow_templates,
                    template_min_literal=template_min_literal,
                )
                if line
                else ""
                for line in lines
            )
    if code != DEFAULT_LANGUAGE:
        translated = UI_PHRASES_BY_LANGUAGE.get(code, {}).get(text)
        if translated is not None:
            return translated
        if allow_templates:
            translated = _translate_reviewed_template(
                text,
                code,
                min_literal_weight=template_min_literal,
            )
            if translated is not None:
                return translated
        dynamic = _translate_dynamic_ui_phrase(text, code)
        if dynamic is not None:
            return dynamic

    key = _SHELL_SOURCE_KEYS.get(text)
    return _translate_for_language(code, key) if key is not None else text


_FORMAT_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_FORMAT_SOURCE_PHRASES = tuple(
    phrase for phrase in SOURCE_PHRASES if _FORMAT_FIELD.search(phrase)
)


@lru_cache(maxsize=None)
def _compiled_source_template(source_template: str) -> re.Pattern[str] | None:
    """Compile one reviewed format template once for all widget translations."""
    cursor = 0
    fields: set[str] = set()
    pattern: list[str] = []
    for match in _FORMAT_FIELD.finditer(source_template):
        pattern.append(re.escape(source_template[cursor : match.start()]))
        name = match.group(1)
        if name in fields:
            pattern.append(f"(?P={name})")
        else:
            # Empty optional framing (for example an absent service note) is
            # valid. Reverse matching is only enabled for explicitly
            # registered launcher-owned widgets, never arbitrary diagnostics.
            pattern.append(f"(?P<{name}>.*?)")
            fields.add(name)
        cursor = match.end()
    if not fields:
        return None
    pattern.append(re.escape(source_template[cursor:]))
    return re.compile("".join(pattern), flags=re.DOTALL)


def _template_match(source_template: str, text: str) -> dict[str, str] | None:
    """Match a rendered string against one reviewed ``str.format`` template."""
    pattern = _compiled_source_template(source_template)
    if pattern is None:
        return None
    rendered = pattern.fullmatch(text)
    return rendered.groupdict() if rendered is not None else None


def _translate_reviewed_template(
    text: str,
    language: str,
    *,
    min_literal_weight: int = 0,
) -> str | None:
    """Translate reviewed formatted UI while leaving inserted values untouched."""
    catalog = UI_PHRASES_BY_LANGUAGE.get(language, {})
    candidates: list[tuple[int, str, dict[str, str]]] = []
    for source_template in _FORMAT_SOURCE_PHRASES:
        values = _template_match(source_template, text)
        if values is None:
            continue
        translated_template = catalog.get(source_template)
        if translated_template is None:
            continue
        literal_weight = len(_FORMAT_FIELD.sub("", source_template))
        if literal_weight < min_literal_weight:
            continue
        candidates.append((literal_weight, translated_template, values))
    for _weight, translated_template, values in sorted(
        candidates,
        key=lambda candidate: candidate[0],
        reverse=True,
    ):
        try:
            return translated_template.format(**values)
        except (KeyError, ValueError):
            continue
    return None


def is_reviewed_ui_phrase(
    text: str,
    *,
    allow_templates: bool = False,
) -> bool:
    """Return whether *text* belongs to the launcher-owned translation corpus."""
    if not text:
        return False
    if "\n" in text:
        lines = text.split("\n")
        return all(
            not line
            or is_reviewed_ui_phrase(line, allow_templates=allow_templates)
            for line in lines
        )
    if text in SOURCE_PHRASE_SET or text in _SHELL_SOURCE_KEYS:
        return True
    if _translate_dynamic_ui_phrase(text, "zh_CN") is not None:
        return True
    return allow_templates and any(
        _template_match(source_template, text) is not None
        for source_template in _FORMAT_SOURCE_PHRASES
    )


def format_ui_phrase(
    source_template: str,
    /,
    language: object | None = None,
    **values: object,
) -> str:
    """Format one reviewed launcher phrase in the requested UI language."""
    code = _current_language if language is None else normalize_language(language)
    template = source_template
    if code != DEFAULT_LANGUAGE:
        template = UI_PHRASES_BY_LANGUAGE.get(code, {}).get(
            source_template,
            source_template,
        )
    try:
        return template.format(**values)
    except (KeyError, ValueError):
        return source_template.format(**values)


def _translate_dynamic_ui_phrase(text: str, language: str) -> str | None:
    """Translate the small reviewed set of numeric runtime labels."""
    runtime_summary = re.fullmatch(
        r"Game (.+?) · Market (.+?) · (\d+) clients?",
        text,
    )
    if runtime_summary is not None:
        template = UI_PHRASES_BY_LANGUAGE.get(language, {}).get(
            "Game {game_state} · Market {market_state} · {count} client(s)"
        )
        if template is not None:
            return template.format(
                game_state=translate_ui_phrase(
                    runtime_summary.group(1).capitalize(),
                    language,
                ),
                market_state=translate_ui_phrase(
                    runtime_summary.group(2).capitalize(),
                    language,
                ),
                count=runtime_summary.group(3),
            )

    eve_clients = re.fullmatch(r"(\d+) EVE clients? running", text)
    if eve_clients is not None:
        template = UI_PHRASES_BY_LANGUAGE.get(language, {}).get(
            "{count} EVE client(s) running"
        )
        if template is not None:
            return template.format(count=eve_clients.group(1))

    patterns = (
        (r"(\d+) available", "available"),
        (r"(\d+) tools?", "tools"),
        (r"(\d+) clients? running", "clients"),
        (r"(\d+) characters?", "characters"),
    )
    match = None
    kind = ""
    for pattern, candidate_kind in patterns:
        match = re.fullmatch(pattern, text)
        if match is not None:
            kind = candidate_kind
            break
    if match is None:
        return None

    count_text = match.group(1)
    count = int(count_text)
    if language == "ru":
        forms = {
            "available": ("доступен", "доступно", "доступно"),
            "tools": ("инструмент", "инструмента", "инструментов"),
            "clients": (
                "запущенный клиент",
                "запущенных клиента",
                "запущенных клиентов",
            ),
            "characters": ("персонаж", "персонажа", "персонажей"),
        }
        one, few, many = forms[kind]
        if count % 10 == 1 and count % 100 != 11:
            noun = one
        elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
            noun = few
        else:
            noun = many
        return f"{count_text} {noun}"

    templates: dict[str, dict[str, tuple[str, str]]] = {
        "zh_CN": {
            "available": ("{count} 个可用", "{count} 个可用"),
            "tools": ("{count} 个工具", "{count} 个工具"),
            "clients": ("{count} 个客户端运行中", "{count} 个客户端运行中"),
            "characters": ("{count} 个角色", "{count} 个角色"),
        },
        "ja": {
            "available": ("{count} 件利用可能", "{count} 件利用可能"),
            "tools": ("ツール {count} 件", "ツール {count} 件"),
            "clients": ("クライアント {count} 件が実行中", "クライアント {count} 件が実行中"),
            "characters": ("キャラクター {count} 人", "キャラクター {count} 人"),
        },
        "ko": {
            "available": ("{count}개 사용 가능", "{count}개 사용 가능"),
            "tools": ("도구 {count}개", "도구 {count}개"),
            "clients": ("클라이언트 {count}개 실행 중", "클라이언트 {count}개 실행 중"),
            "characters": ("캐릭터 {count}명", "캐릭터 {count}명"),
        },
        "fr": {
            "available": ("{count} disponible", "{count} disponibles"),
            "tools": ("{count} outil", "{count} outils"),
            "clients": ("{count} client en cours", "{count} clients en cours"),
            "characters": ("{count} personnage", "{count} personnages"),
        },
        "de": {
            "available": ("{count} verfügbar", "{count} verfügbar"),
            "tools": ("{count} Werkzeug", "{count} Werkzeuge"),
            "clients": ("{count} Client läuft", "{count} Clients laufen"),
            "characters": ("{count} Charakter", "{count} Charaktere"),
        },
        "nl": {
            "available": ("{count} beschikbaar", "{count} beschikbaar"),
            "tools": ("{count} hulpmiddel", "{count} hulpmiddelen"),
            "clients": ("{count} client actief", "{count} clients actief"),
            "characters": ("{count} personage", "{count} personages"),
        },
    }
    language_templates = templates.get(language)
    if language_templates is None:
        return None
    singular, plural = language_templates[kind]
    return (singular if count == 1 else plural).format(count=count_text)


def missing_ui_phrase_translations() -> dict[str, set[str]]:
    """Return missing reviewed phrases per non-English language."""
    expected = SOURCE_PHRASE_SET
    return {
        option.code: expected - set(UI_PHRASES_BY_LANGUAGE.get(option.code, {}))
        for option in LANGUAGES
        if option.code != DEFAULT_LANGUAGE
    }


_SERVICE_SOURCE_KEYS = {
    "▶ Start {service}": "service.start",
    "⏳ Starting {service}…": "service.starting",
    "■ Stop {service}": "service.stop",
    "⏳ Stopping {service}…": "service.stopping",
    "↻ Retry {service}": "service.retry",
    "{service}: Starting…": "service.state_starting",
    "{service}: Stopping…": "service.state_stopping",
    "{service}: Starting...": "service.state_starting",
    "{service}: Stopping...": "service.state_stopping",
    "{service}: Unknown": "service.unknown",
    "{service}: Docker unavailable": "service.docker_unavailable",
    "{service}: Offline": "service.offline",
    "{service}: Online": "service.online",
    "{service}: Failed": "service.failed",
    "{service}: External": "service.external",
}


def translate_service_action(source: str) -> str:
    """Translate one structured English Server/Market action from app state."""
    # English is already the source language. Preserve its exact punctuation
    # (notably the UI's intentional mix of typographic ellipses and three-dot
    # status text) instead of normalizing it through the shell catalog.
    if _current_language == DEFAULT_LANGUAGE:
        return source
    for english_name, service_key in (
        ("Server", "nav.server"),
        ("Market", "nav.market"),
    ):
        if source == english_name:
            return translate(service_key)
        for template, translation_key in _SERVICE_SOURCE_KEYS.items():
            if source == template.format(service=english_name):
                return translate(
                    translation_key,
                    service=translate(service_key),
                )
    return source


_SERVICE_TOOLTIP_SOURCE_KEYS = {
    "{service} is changing state": "tooltip.service_changing",
    (
        "{service} was started outside this launcher and must be stopped "
        "from its original console"
    ): "tooltip.service_external",
    "{service} is starting": "tooltip.service_starting",
    "{service} is stopping": "tooltip.service_stopping",
}

_GENERAL_TOOLTIP_SOURCE_KEYS = {
    "Connect-only Docker mode cannot change containers.": (
        "tooltip.docker_connect_only"
    ),
    "Docker state is unavailable": "tooltip.docker_unavailable",
    "Stop Server first": "tooltip.stop_server_first",
}


def translate_service_tooltip(source: str) -> str:
    """Translate one structured service-control explanation."""
    general_key = _GENERAL_TOOLTIP_SOURCE_KEYS.get(source)
    if general_key is not None:
        return translate(general_key)
    for english_name, service_key in (
        ("Server", "nav.server"),
        ("Market", "nav.market"),
    ):
        for template, translation_key in _SERVICE_TOOLTIP_SOURCE_KEYS.items():
            if source == template.format(service=english_name):
                return translate(
                    translation_key,
                    service=translate(service_key),
                )
    return source
