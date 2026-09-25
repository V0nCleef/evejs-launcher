"""Reviewed translations for launcher-owned automatic-login framing."""
from __future__ import annotations

LANGUAGE_CODES = ("zh_CN", "ja", "ko", "fr", "de", "nl", "ru")

_PHRASES: dict[str, tuple[str, ...]] = {
    "Client checks passed. Docker server compatibility is checked when a character launches.": (
        "客户端检查通过。角色启动时会检查 Docker 服务器兼容性。",
        "クライアントの検査に合格しました。キャラクターの起動時に Docker サーバーとの互換性を確認します。",
        "클라이언트 검사를 통과했습니다. 캐릭터를 시작할 때 Docker 서버 호환성을 확인합니다.",
        "Les vérifications du client ont réussi. La compatibilité du serveur Docker est vérifiée au lancement d’un personnage.",
        "Die Clientprüfung war erfolgreich. Die Docker-Serverkompatibilität wird beim Start eines Charakters geprüft.",
        "De clientcontrole is geslaagd. De compatibiliteit met de Docker-server wordt gecontroleerd wanneer een personage wordt gestart.",
        "Проверка клиента пройдена. Совместимость Docker-сервера проверяется при запуске персонажа.",
    ),
}

SOURCE_PHRASES = tuple(_PHRASES)
UI_PHRASES_BY_LANGUAGE = {
    language: {source: values[index] for source, values in _PHRASES.items()}
    for index, language in enumerate(LANGUAGE_CODES)
}
