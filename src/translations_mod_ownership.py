"""Reviewed ownership-repair copy for every supported non-English language."""
LANGUAGE_CODES = ('zh_CN', 'ja', 'ko', 'fr', 'de', 'nl', 'ru')

_PHRASES = {
    'OWNERSHIP': (
        '归属待确认', '管理情報', '관리 정보', 'PROPRIÉTÉ', 'ZUORDNUNG', 'BEHEER', 'ВЛАДЕНИЕ',
    ),
    'REPAIR REQUIRED': (
        '需要修复', '修復が必要', '복구 필요', 'RÉPARATION REQUISE', 'REPARATUR NÖTIG', 'HERSTEL NODIG', 'НУЖНО ИСПРАВЛЕНИЕ',
    ),
    'Review mod ownership using the banner above before changing mods.': (
        '更改模组前，请通过上方提示检查模组归属。',
        'モッドを変更する前に、上の案内から管理情報を確認してください。',
        '모드를 변경하기 전에 위 안내에서 모드 관리 정보를 검토하세요.',
        'Vérifiez la propriété des mods dans le bandeau ci-dessus avant de les modifier.',
        'Prüfe vor Mod-Änderungen die Zuordnung über den Hinweis oben.',
        'Controleer het modbeheer via de melding hierboven voordat je mods wijzigt.',
        'Перед изменением модов проверьте сведения о владении через сообщение выше.',
    ),
    'Mod ownership needs repair': (
        '需要修复模组归属记录', 'モッドの管理情報に修復が必要です', '모드 관리 정보 복구 필요',
        'La propriété des mods doit être corrigée', 'Mod-Zuordnung muss repariert werden',
        'Modbeheer moet worden hersteld', 'Нужно исправить сведения о владении модами',
    ),
    'Duplicate records claim the same mod folder. Review a verified repair before applying mods. Your mod files and enabled states stay as they are.': (
        '多个重复记录指向同一模组文件夹。应用模组前，请检查已验证的修复方案。模组文件和启用状态保持不变。',
        '同じモッドフォルダーに重複する管理記録があります。モッドを適用する前に、検証済みの修復内容を確認してください。モッドのファイルと有効状態は変わりません。',
        '같은 모드 폴더에 중복 관리 기록이 있습니다. 모드를 적용하기 전에 검증된 복구 내용을 검토하세요. 모드 파일과 활성화 상태는 그대로 유지됩니다.',
        'Des enregistrements en double revendiquent le même dossier de mod. Vérifiez la réparation validée avant d’appliquer les mods. Les fichiers et états d’activation restent inchangés.',
        'Doppelte Einträge beanspruchen denselben Mod-Ordner. Prüfe die verifizierte Reparatur, bevor du Mods anwendest. Mod-Dateien und Aktivierungszustände bleiben unverändert.',
        'Dubbele beheerrecords verwijzen naar dezelfde modmap. Bekijk het gecontroleerde herstel voordat je mods toepast. Modbestanden en ingeschakelde toestanden blijven ongewijzigd.',
        'Несколько записей претендуют на одну папку мода. Перед применением модов проверьте подтверждённое исправление. Файлы и состояния включения модов не изменятся.',
    ),
    'Duplicate records claim the same mod folder, but a safe repair could not be verified. Keep the registry for individual review.': (
        '多个重复记录指向同一模组文件夹，但无法验证安全的修复方案。请保留注册记录文件以便逐项检查。',
        '同じモッドフォルダーに重複する管理記録がありますが、安全な修復を確認できませんでした。個別確認のため管理記録ファイルを保管してください。',
        '같은 모드 폴더에 중복 관리 기록이 있지만 안전한 복구를 확인하지 못했습니다. 개별 검토를 위해 관리 기록 파일을 보관하세요.',
        'Des enregistrements en double revendiquent le même dossier, mais aucune réparation sûre n’a pu être vérifiée. Conservez le registre pour un examen individuel.',
        'Doppelte Einträge beanspruchen denselben Mod-Ordner, aber eine sichere Reparatur konnte nicht bestätigt werden. Bewahre die Registry zur Einzelprüfung auf.',
        'Dubbele beheerrecords verwijzen naar dezelfde modmap, maar veilig herstel kon niet worden bevestigd. Bewaar het register voor afzonderlijke controle.',
        'Несколько записей претендуют на одну папку мода, но безопасность исправления не подтверждена. Сохраните реестр для отдельной проверки.',
    ),
    'Review repair': (
        '检查修复方案', '修復内容を確認', '복구 검토', 'Vérifier la réparation', 'Reparatur prüfen', 'Herstel bekijken', 'Проверить исправление',
    ),
    'Review a verified repair of duplicate mod ownership. An exact backup is kept.': (
        '检查已验证的模组归属重复记录修复方案。将保留完全一致的备份。',
        '重複するモッド管理情報の検証済み修復内容を確認します。元の内容の完全なバックアップを保存します。',
        '중복된 모드 관리 정보에 대한 검증된 복구를 검토합니다. 원본과 동일한 백업을 보관합니다.',
        'Vérifiez la réparation validée des doublons de propriété. Une sauvegarde exacte est conservée.',
        'Prüfe die verifizierte Reparatur doppelter Mod-Zuordnungen. Eine exakte Sicherung wird aufbewahrt.',
        'Bekijk het gecontroleerde herstel van dubbele modbeheerrecords. Een exacte reservekopie blijft bewaard.',
        'Проверьте подтверждённое исправление дублирующихся записей о владении. Точная резервная копия сохраняется.',
    ),
    'A safe ownership repair could not be verified. Keep the registry for individual review.': (
        '无法验证安全的归属修复方案。请保留注册记录文件以便逐项检查。',
        '管理情報を安全に修復できるか確認できませんでした。個別確認のため管理記録ファイルを保管してください。',
        '안전한 관리 정보 복구를 확인하지 못했습니다. 개별 검토를 위해 관리 기록 파일을 보관하세요.',
        'Aucune réparation sûre de la propriété n’a pu être vérifiée. Conservez le registre pour un examen individuel.',
        'Eine sichere Reparatur der Zuordnung konnte nicht bestätigt werden. Bewahre die Registry zur Einzelprüfung auf.',
        'Veilig herstel van het modbeheer kon niet worden bevestigd. Bewaar het register voor afzonderlijke controle.',
        'Безопасность исправления сведений о владении не подтверждена. Сохраните реестр для отдельной проверки.',
    ),
    'Wait for the active server or mod operation to finish before repairing ownership.': (
        '请等待当前服务器或模组操作完成后，再修复归属记录。',
        '実行中のサーバーまたはモッド操作が完了してから管理情報を修復してください。',
        '진행 중인 서버 또는 모드 작업이 완료된 후 관리 정보를 복구하세요.',
        'Attendez la fin de l’opération de serveur ou de mod avant de corriger la propriété.',
        'Warte, bis der laufende Server- oder Mod-Vorgang beendet ist, bevor du die Zuordnung reparierst.',
        'Wacht tot de actieve server- of modbewerking is voltooid voordat je het modbeheer herstelt.',
        'Перед исправлением сведений о владении дождитесь завершения текущей операции сервера или мода.',
    ),
    'Repair Mod Ownership': (
        '修复模组归属记录', 'モッド管理情報の修復', '모드 관리 정보 복구', 'Corriger la propriété des mods',
        'Mod-Zuordnung reparieren', 'Modbeheer herstellen', 'Исправить сведения о владении модами',
    ),
    'The selected folder, runtime or repair preview changed. Nothing was changed; refresh Mods.': (
        '所选文件夹、运行环境或修复预览已更改。未进行任何修改，请刷新模组页面。',
        '選択したフォルダー、実行環境、または修復プレビューが変わりました。変更は行っていません。モッド画面を更新してください。',
        '선택한 폴더, 실행 환경 또는 복구 미리보기가 변경되었습니다. 아무것도 변경하지 않았습니다. 모드 화면을 새로 고치세요.',
        'Le dossier sélectionné, l’environnement ou l’aperçu de réparation a changé. Rien n’a été modifié ; actualisez les mods.',
        'Ordner, Laufzeitumgebung oder Reparaturvorschau haben sich geändert. Es wurde nichts geändert; aktualisiere Mods.',
        'De geselecteerde map, uitvoeromgeving of herstelvoorvertoning is gewijzigd. Er is niets aangepast; vernieuw Mods.',
        'Выбранная папка, среда выполнения или план исправления изменились. Ничего не изменено; обновите страницу модов.',
    ),
    'Additional folders: {count}': (
        '其他文件夹：{count}', 'その他のフォルダー: {count}', '추가 폴더: {count}',
        'Dossiers supplémentaires : {count}', 'Weitere Ordner: {count}', 'Overige mappen: {count}', 'Другие папки: {count}',
    ),
    'Repair duplicate mod records?\n\nFolders: {folders}\n\nVerified redundant records: {count}\n\nKeep the matching owner, current mod order and enabled states. An exact backup is kept. Mod files, settings and saved data are not changed.': (
        '修复重复的模组记录？\n\n文件夹：{folders}\n\n已验证的多余记录：{count}\n\n保留匹配的归属记录、当前模组顺序和启用状态。将保留完全一致的备份。模组文件、设置和存档数据不会改变。',
        '重複するモッド管理記録を修復しますか？\n\nフォルダー: {folders}\n\n確認済みの余分な記録: {count}\n\n一致する管理記録、現在のモッド順序、有効状態を維持します。元の内容の完全なバックアップを保存します。モッドのファイル、設定、セーブデータは変更しません。',
        '중복 모드 기록을 복구할까요?\n\n폴더: {folders}\n\n확인된 중복 기록: {count}\n\n일치하는 관리 기록, 현재 모드 순서 및 활성화 상태를 유지합니다. 원본과 동일한 백업을 보관합니다. 모드 파일, 설정 및 저장 데이터는 변경하지 않습니다.',
        'Corriger les enregistrements de mods en double ?\n\nDossiers : {folders}\n\nEnregistrements redondants vérifiés : {count}\n\nConserver le propriétaire correspondant, l’ordre actuel des mods et leurs états d’activation. Une sauvegarde exacte est conservée. Les fichiers des mods, paramètres et sauvegardes ne sont pas modifiés.',
        'Doppelte Mod-Einträge reparieren?\n\nOrdner: {folders}\n\nVerifizierte überflüssige Einträge: {count}\n\nPassenden Besitzer, aktuelle Mod-Reihenfolge und Aktivierungszustände beibehalten. Eine exakte Sicherung wird aufbewahrt. Mod-Dateien, Einstellungen und Spielstände werden nicht geändert.',
        'Dubbele modbeheerrecords herstellen?\n\nMappen: {folders}\n\nGecontroleerde overbodige records: {count}\n\nBehoud de overeenkomende eigenaar, huidige modvolgorde en ingeschakelde toestanden. Een exacte reservekopie blijft bewaard. Modbestanden, instellingen en opgeslagen gegevens worden niet gewijzigd.',
        'Исправить дублирующиеся записи модов?\n\nПапки: {folders}\n\nПроверенные избыточные записи: {count}\n\nСохранить соответствующего владельца, текущий порядок модов и состояния включения. Точная резервная копия сохраняется. Файлы модов, настройки и сохранённые данные не изменяются.',
    ),
    'Mod Ownership Repaired': (
        '模组归属记录已修复', 'モッド管理情報を修復しました', '모드 관리 정보 복구 완료',
        'Propriété des mods corrigée', 'Mod-Zuordnung repariert', 'Modbeheer hersteld', 'Сведения о владении модами исправлены',
    ),
    'Duplicate ownership records were repaired.\n\nBackup: {path}': (
        '已修复重复的归属记录。\n\n备份：{path}',
        '重複する管理記録を修復しました。\n\nバックアップ: {path}',
        '중복된 관리 기록을 복구했습니다.\n\n백업: {path}',
        'Les doublons de propriété ont été corrigés.\n\nSauvegarde : {path}',
        'Doppelte Zuordnungseinträge wurden repariert.\n\nSicherung: {path}',
        'Dubbele beheerrecords zijn hersteld.\n\nReservekopie: {path}',
        'Дублирующиеся записи о владении исправлены.\n\nРезервная копия: {path}',
    ),
    'This mod folder is still registered, even though its files are missing. Restore the registered folder or choose a different folder name.': (
        '此模组文件夹仍有注册记录，但文件已缺失。请恢复已注册的文件夹，或选择其他文件夹名称。',
        'ファイルがなくても、このモッドフォルダーの管理記録は残っています。記録済みのフォルダーを復元するか、別のフォルダー名を選んでください。',
        '파일이 없어도 이 모드 폴더의 관리 기록은 남아 있습니다. 등록된 폴더를 복원하거나 다른 폴더 이름을 선택하세요.',
        'Ce dossier de mod est toujours enregistré, même si ses fichiers sont absents. Restaurez-le ou choisissez un autre nom de dossier.',
        'Dieser Mod-Ordner ist noch registriert, obwohl seine Dateien fehlen. Stelle den registrierten Ordner wieder her oder wähle einen anderen Ordnernamen.',
        'Deze modmap is nog geregistreerd, ook al ontbreken de bestanden. Herstel de geregistreerde map of kies een andere mapnaam.',
        'Эта папка мода всё ещё зарегистрирована, хотя её файлы отсутствуют. Восстановите зарегистрированную папку или выберите другое имя.',
    ),
}

assert all(len(values) == len(LANGUAGE_CODES) for values in _PHRASES.values())
SOURCE_PHRASES = tuple(_PHRASES)
UI_PHRASES_BY_LANGUAGE = {
    language: {source: values[index] for source, values in _PHRASES.items()}
    for index, language in enumerate(LANGUAGE_CODES)
}
