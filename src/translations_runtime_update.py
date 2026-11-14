"""Runtime-preserving update diagnostics for all supported languages."""
SOURCE_PHRASES = (
    "Running services or clients could not be safely prepared for this update. Check the launcher logs and try again.",
)
_VALUES = {
    "zh_CN": "无法安全地为本次更新准备正在运行的服务或客户端。请检查启动器日志后重试。",
    "ja": "実行中のサービスまたはクライアントを、この更新に向けて安全に準備できませんでした。ランチャーのログを確認して再試行してください。",
    "ko": "실행 중인 서비스 또는 클라이언트를 이 업데이트에 맞게 안전하게 준비하지 못했습니다. 런처 로그를 확인하고 다시 시도하세요.",
    "fr": "Les services ou clients en cours n’ont pas pu être préparés en toute sécurité pour cette mise à jour. Consultez les journaux du lanceur et réessayez.",
    "de": "Laufende Dienste oder Clients konnten nicht sicher auf dieses Update vorbereitet werden. Prüfe die Launcher-Protokolle und versuche es erneut.",
    "nl": "De actieve services of clients konden niet veilig worden voorbereid op deze update. Controleer de launcherlogs en probeer het opnieuw.",
    "ru": "Не удалось безопасно подготовить работающие службы или клиенты к обновлению. Проверьте журнал лаунчера и повторите попытку.",
}
UI_PHRASES_BY_LANGUAGE = {code: {SOURCE_PHRASES[0]: value} for code, value in _VALUES.items()}
