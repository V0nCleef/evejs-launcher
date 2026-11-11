"""Reviewed sorter controls for every launcher language."""
LANGUAGE_CODES = ("zh_CN", "ja", "ko", "fr", "de", "nl", "ru")
_PHRASES = {
    "Sort by dependencies": ("按依赖关系排序", "依存関係で並べ替え", "의존성에 따라 정렬", "Trier par dépendances", "Nach Abhängigkeiten sortieren", "Sorteren op afhankelijkheden", "Сортировать по зависимостям"),
    "Review the saved order against declared dependencies and load rules. Mods without ordering rules keep their relative order.": (
        "对比已保存的顺序与模组声明的依赖及加载规则。没有排序规则的模组保持彼此的先后顺序。",
        "保存済みの順序と、宣言された依存関係・読み込み規則を比較します。順序の規則がない Mod 同士の前後関係は維持されます。",
        "저장된 순서를 선언된 의존성과 로드 규칙에 따라 검토합니다. 순서 규칙이 없는 모드끼리의 상대적인 순서는 유지됩니다.",
        "Comparez l’ordre enregistré aux dépendances et règles de chargement déclarées. Les mods sans règle d’ordre conservent leur ordre relatif.",
        "Die gespeicherte Reihenfolge mit den angegebenen Abhängigkeiten und Laderegeln vergleichen. Mods ohne Reihenfolgeregeln behalten ihre relative Reihenfolge.",
        "Vergelijk de opgeslagen volgorde met de opgegeven afhankelijkheden en laadregels. Mods zonder volgorderegels behouden hun onderlinge volgorde.",
        "Сравните сохранённый порядок с заявленными зависимостями и правилами загрузки. Моды без правил порядка сохраняют взаимное расположение."),
    "Saved order": ("已保存的顺序", "保存済みの順序", "저장된 순서", "Ordre enregistré", "Gespeicherte Reihenfolge", "Opgeslagen volgorde", "Сохранённый порядок"),
    "Proposed order": ("建议的顺序", "提案された順序", "제안된 순서", "Ordre proposé", "Vorgeschlagene Reihenfolge", "Voorgestelde volgorde", "Предлагаемый порядок"),
    "Requires": ("需要", "必須", "필수 항목", "Nécessite", "Benötigt", "Vereist", "Требует"),
    "Load before": ("在此之前加载", "先に読み込む", "다음보다 먼저 로드", "Charger avant", "Laden vor", "Laden vóór", "Загрузить до"),
    "Load after": ("在此之后加载", "後に読み込む", "다음보다 나중에 로드", "Charger après", "Laden nach", "Laden na", "Загрузить после"),
    "Conflicts with": ("与此冲突", "競合", "충돌 항목", "En conflit avec", "Konflikt mit", "Conflicteert met", "Конфликтует с"),
    "No declared relationships": ("未声明依赖关系", "関係の宣言なし", "선언된 관계 없음", "Aucune relation déclarée", "Keine Beziehungen angegeben", "Geen relaties opgegeven", "Связи не заявлены"),
    "Resolve the listed issues before saving an order.": ("请先解决列出的问题，再保存顺序。", "一覧の問題を解決してから順序を保存してください。", "나열된 문제를 해결한 후 순서를 저장하세요.", "Résolvez les problèmes indiqués avant d’enregistrer un ordre.", "Vor dem Speichern der Reihenfolge die aufgeführten Probleme beheben.", "Los de vermelde problemen op voordat je de volgorde opslaat.", "Устраните перечисленные проблемы перед сохранением порядка."),
    "No order changes are needed.": ("无需调整顺序。", "順序を変更する必要はありません。", "순서를 변경할 필요가 없습니다.", "Aucun changement d’ordre n’est nécessaire.", "Keine Änderung der Reihenfolge erforderlich.", "De volgorde hoeft niet te worden gewijzigd.", "Изменять порядок не требуется."),
    "Save this order, then use Apply & Restart when you are ready.": ("保存此顺序，准备就绪后使用“应用并重启”。", "この順序を保存し、準備ができたら「適用して再起動」を実行してください。", "이 순서를 저장하고 준비가 되면 ‘적용 및 재시작’을 사용하세요.", "Enregistrez cet ordre, puis utilisez Appliquer et redémarrer quand vous êtes prêt.", "Diese Reihenfolge speichern und bei Bedarf „Anwenden und neu starten“ verwenden.", "Sla deze volgorde op en gebruik Toepassen en herstarten zodra je zover bent.", "Сохраните этот порядок и воспользуйтесь «Применить и перезапустить», когда будете готовы."),
    "Sorting cannot repair incompatible mods or shared login code. No mods are enabled and no server is restarted by this action.": (
        "排序无法修复不兼容的模组或共享登录代码。此操作不会启用模组，也不会重启服务器。",
        "並べ替えでは互換性のない Mod や共有ログインコードを修復できません。この操作で Mod が有効化されたり、サーバーが再起動されたりすることはありません。",
        "정렬로 호환되지 않는 모드나 공유 로그인 코드를 복구할 수는 없습니다. 이 작업은 모드를 활성화하거나 서버를 재시작하지 않습니다.",
        "Le tri ne peut pas corriger les mods incompatibles ni le code de connexion partagé. Cette action n’active aucun mod et ne redémarre aucun serveur.",
        "Sortieren repariert keine inkompatiblen Mods oder gemeinsamen Anmeldecode. Dabei werden weder Mods aktiviert noch Server neu gestartet.",
        "Sorteren repareert geen incompatibele mods of gedeelde inlogcode. Deze actie schakelt geen mods in en herstart de server niet.",
        "Сортировка не исправляет несовместимые моды или общий код входа. Это действие не включает моды и не перезапускает сервер."),
    "Save order": ("保存顺序", "順序を保存", "순서 저장", "Enregistrer l’ordre", "Reihenfolge speichern", "Volgorde opslaan", "Сохранить порядок"),
    "Mod order saved. Use Apply & Restart when you are ready.": ("模组顺序已保存。准备就绪后使用“应用并重启”。", "Mod の順序を保存しました。準備ができたら「適用して再起動」を実行してください。", "모드 순서를 저장했습니다. 준비가 되면 ‘적용 및 재시작’을 사용하세요.", "Ordre des mods enregistré. Utilisez Appliquer et redémarrer quand vous êtes prêt.", "Mod-Reihenfolge gespeichert. Bei Bedarf „Anwenden und neu starten“ verwenden.", "Modvolgorde opgeslagen. Gebruik Toepassen en herstarten zodra je zover bent.", "Порядок модов сохранён. Воспользуйтесь «Применить и перезапустить», когда будете готовы."),
}
SOURCE_PHRASES = tuple(_PHRASES)
UI_PHRASES_BY_LANGUAGE = {language: {source: values[index] for source, values in _PHRASES.items()}
                          for index, language in enumerate(LANGUAGE_CODES)}
