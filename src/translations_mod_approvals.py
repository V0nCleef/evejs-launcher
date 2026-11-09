"""Local compatibility choices, separate from author compatibility declarations."""
_LANGUAGES = ("zh_CN", "ja", "ko", "fr", "de", "nl", "ru")
_ROWS = {
    "Remember selected for EveJS {version}": (
        "记住所选模组对 EveJS {version} 的批准", "選択したMODの承認を EveJS {version} 用に保存", "선택한 모드의 EveJS {version} 승인 기억",
        "Mémoriser la sélection pour EveJS {version}", "Auswahl für EveJS {version} merken", "Geselecteerde mods onthouden voor EveJS {version}", "Запомнить выбранные для EveJS {version}",
    ),
    "Forget local approvals": (
        "清除本地批准", "ローカル承認を削除", "로컬 승인 지우기", "Effacer les approbations locales", "Lokale Freigaben vergessen", "Lokale goedkeuringen wissen", "Сбросить локальные разрешения",
    ),
    "Local user approvals are separate from author support.": (
        "本地用户批准与作者支持声明相互独立。", "ローカルのユーザー承認は作者の対応宣言とは別です。", "로컬 사용자 승인은 제작자의 지원 선언과 별개입니다.",
        "Les approbations locales sont distinctes de la prise en charge par l’auteur.", "Lokale Benutzerfreigaben sind von der Unterstützung durch den Autor getrennt.", "Lokale goedkeuringen staan los van ondersteuning door de maker.", "Локальные разрешения пользователя не означают поддержку автора.",
    ),
    "Local user approval for selected mods, this installation and backend. Changes to mod files or EveJS require approval again. Author support is unchanged.": (
        "本地批准所选模组，仅适用于此安装和后端。模组文件或 EveJS 更改后需要重新批准。作者支持声明不变。",
        "選択したMODをこのインストールとバックエンドでローカル承認します。MODファイルやEveJSが変わると再承認が必要です。作者の対応宣言は変わりません。",
        "선택한 모드를 이 설치와 백엔드에서 로컬 승인합니다. 모드 파일이나 EveJS가 바뀌면 다시 승인해야 합니다. 제작자의 지원 선언은 유지됩니다.",
        "Approbation locale des mods sélectionnés pour cette installation et ce backend. Toute modification des fichiers ou d’EveJS exige une nouvelle approbation. La déclaration de l’auteur reste inchangée.",
        "Lokale Freigabe der ausgewählten Mods für diese Installation und dieses Backend. Änderungen an Mod-Dateien oder EveJS erfordern eine neue Freigabe. Die Autorenangaben bleiben unverändert.",
        "Lokale goedkeuring voor geselecteerde mods, deze installatie en deze backend. Na wijzigingen aan modbestanden of EveJS is opnieuw goedkeuring nodig. De verklaring van de maker blijft ongewijzigd.",
        "Локальное разрешение выбранных модов для этой установки и бэкенда. Изменения файлов модов или EveJS потребуют нового разрешения. Заявление автора не меняется.",
    ),
    "Select the mods to remember. Unselected mods will be reviewed next.": (
        "选择要记住的模组。随后将继续审核未选中的模组。", "承認を保存するMODを選んでください。未選択のMODは次に確認します。", "승인을 기억할 모드를 선택하세요. 선택하지 않은 모드는 다음에 검토합니다.",
        "Sélectionnez les mods à mémoriser. Les autres seront examinés ensuite.", "Mods zum Merken auswählen. Die übrigen Mods werden anschließend geprüft.", "Selecteer welke mods je wilt onthouden. Daarna beoordeel je de overige mods.", "Выберите моды для сохранения разрешения. Остальные будут рассмотрены далее.",
    ),
    "Remember {mod}": (
        "记住 {mod} 的批准", "{mod} の承認を保存", "{mod} 승인 기억", "Mémoriser {mod}", "{mod} merken", "{mod} onthouden", "Запомнить разрешение для {mod}",
    ),
    "Declares EveJS: {versions}": (
        "声明支持 EveJS：{versions}", "対応宣言の EveJS: {versions}", "지원 선언 EveJS: {versions}", "EveJS déclarés : {versions}", "Deklariert EveJS: {versions}", "Verklaarde EveJS-versies: {versions}", "Заявленные версии EveJS: {versions}",
    ),
    "Run once": (
        "仅运行一次", "今回のみ実行", "한 번 실행", "Exécuter une fois", "Einmal ausführen", "Eenmalig uitvoeren", "Запустить один раз",
    ),
    "Disable remaining mods": (
        "禁用剩余模组", "残りのMODを無効化", "나머지 모드 비활성화", "Désactiver les mods restants", "Übrige Mods deaktivieren", "Overige mods uitschakelen", "Отключить оставшиеся моды",
    ),
    "Show compatibility prompts again for this installation. Mod files and author declarations stay unchanged.": (
        "为此安装重新显示兼容性提示。模组文件和作者声明保持不变。", "このインストールで互換性の確認を再表示します。MODファイルと作者の宣言は変わりません。", "이 설치에서 호환성 확인을 다시 표시합니다. 모드 파일과 제작자의 선언은 바뀌지 않습니다.",
        "Réafficher les demandes de compatibilité pour cette installation. Les fichiers et déclarations des auteurs restent inchangés.", "Kompatibilitätsabfragen für diese Installation wieder anzeigen. Mod-Dateien und Autorenangaben bleiben unverändert.", "Compatibiliteitsvragen voor deze installatie opnieuw tonen. Modbestanden en verklaringen van makers blijven ongewijzigd.", "Снова показывать запросы совместимости для этой установки. Файлы модов и заявления авторов не меняются.",
    ),
    "Local approvals forgotten. Mod files and author declarations were not changed.": (
        "本地批准已清除。模组文件和作者声明未更改。", "ローカル承認を削除しました。MODファイルと作者の宣言は変更していません。", "로컬 승인을 지웠습니다. 모드 파일과 제작자의 선언은 바뀌지 않았습니다.",
        "Approbations locales effacées. Les fichiers et déclarations des auteurs sont inchangés.", "Lokale Freigaben gelöscht. Mod-Dateien und Autorenangaben wurden nicht geändert.", "Lokale goedkeuringen gewist. Modbestanden en verklaringen van makers zijn niet gewijzigd.", "Локальные разрешения сброшены. Файлы модов и заявления авторов не изменены.",
    ),
}
SOURCE_PHRASES = tuple(_ROWS)
UI_PHRASES_BY_LANGUAGE = {language: {key: values[index] for key, values in _ROWS.items()} for index, language in enumerate(_LANGUAGES)}
