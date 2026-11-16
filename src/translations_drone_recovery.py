"""Drone recovery copy in every supported launcher language."""
LANGUAGES = ('zh_CN', 'ja', 'ko', 'fr', 'de', 'nl', 'ru')
_ROWS = {
    'Character': ('角色', 'キャラクター', '캐릭터', 'Personnage', 'Charakter', 'Personage', 'Персонаж'),
    'Groups': ('分组', 'グループ', '그룹', 'Groupes', 'Gruppen', 'Groepen', 'Группы'),
    'Recover drone groups': ('恢复无人机分组', 'ドローングループを復元', '드론 그룹 복구', 'Restaurer les groupes de drones', 'Drohnengruppen wiederherstellen', 'Dronegroepen herstellen', 'Восстановить группы дронов'),
    'Restore verified drone memberships for all characters from saved launcher profiles. Close EVE clients first.': (
        '从启动器保存的配置中为所有角色恢复已验证的无人机分组成员。请先关闭 EVE 客户端。',
        '保存済みのプロファイルから、全キャラクターの確認済みドローングループ所属を復元します。先に EVE クライアントを終了してください。',
        '저장된 런처 프로필에서 모든 캐릭터의 확인된 드론 그룹 구성을 복구합니다. 먼저 EVE 클라이언트를 닫으세요.',
        'Restaure les appartenances vérifiées aux groupes de drones de tous les personnages depuis les profils sauvegardés. Fermez d’abord les clients EVE.',
        'Stellt geprüfte Drohnenzuordnungen aller Charaktere aus gespeicherten Launcher-Profilen wieder her. EVE-Clients zuerst schließen.',
        'Herstel gecontroleerde dronegroepsleden voor alle personages uit opgeslagen launcherprofielen. Sluit eerst alle EVE-clients.',
        'Восстанавливает проверенный состав групп дронов всех персонажей из сохранённых профилей лаунчера. Сначала закройте клиенты EVE.'),
    'Recovery source:': ('恢复来源：', '復元元：', '복구 원본:', 'Source de restauration :', 'Wiederherstellungsquelle:', 'Herstelbron:', 'Источник восстановления:'),
    'Destination: {root}': ('目标：{root}', '復元先：{root}', '복구 대상: {root}', 'Destination : {root}', 'Ziel: {root}', 'Doel: {root}', 'Назначение: {root}'),
    'Scanning saved drone groups…': ('正在扫描保存的无人机分组…', '保存済みドローングループを確認中…', '저장된 드론 그룹 확인 중…', 'Analyse des groupes de drones sauvegardés…', 'Gespeicherte Drohnengruppen werden geprüft…', 'Opgeslagen dronegroepen worden gecontroleerd…', 'Проверка сохранённых групп дронов…'),
    'Restoring drone groups…': ('正在恢复无人机分组…', 'ドローングループを復元中…', '드론 그룹 복구 중…', 'Restauration des groupes de drones…', 'Drohnengruppen werden wiederhergestellt…', 'Dronegroepen worden hersteld…', 'Восстановление групп дронов…'),
    'Restore groups': ('恢复分组', 'グループを復元', '그룹 복구', 'Restaurer les groupes', 'Gruppen wiederherstellen', 'Groepen herstellen', 'Восстановить группы'),
    'Scan again': ('重新扫描', '再確認', '다시 확인', 'Relancer l’analyse', 'Erneut prüfen', 'Opnieuw controleren', 'Проверить снова'),
    'Drones to restore': ('待恢复的无人机', '復元するドローン数', '복구할 드론 수', 'Drones à restaurer', 'Wiederherzustellende Drohnen', 'Te herstellen drones', 'Дронов к восстановлению'),
    'No verified missing memberships were found. Current groups were kept.': (
        '未发现可验证的缺失成员。当前分组已保留。', '復元可能な確認済みの所属情報はありません。現在のグループは保持されています。',
        '복구할 수 있는 확인된 누락 구성원이 없습니다. 현재 그룹을 유지했습니다.',
        'Aucune appartenance manquante vérifiable trouvée. Les groupes actuels ont été conservés.',
        'Keine überprüfbaren fehlenden Zuordnungen gefunden. Aktuelle Gruppen bleiben erhalten.',
        'Geen gecontroleerde ontbrekende groepsleden gevonden. De huidige groepen zijn behouden.',
        'Проверяемых утраченных назначений не найдено. Текущие группы сохранены.'),
    'Group recovery complete. Launch characters through this launcher to use the recovered memberships.': (
        '分组恢复完成。请通过此启动器启动角色以使用恢复的成员。',
        'グループの復元が完了しました。復元した所属情報を使うには、このランチャーからキャラクターを起動してください。',
        '그룹 복구가 완료되었습니다. 복구된 구성을 사용하려면 이 런처로 캐릭터를 실행하세요.',
        'Restauration terminée. Lancez les personnages depuis ce lanceur pour utiliser les appartenances restaurées.',
        'Wiederherstellung abgeschlossen. Charaktere über diesen Launcher starten, um die wiederhergestellten Zuordnungen zu verwenden.',
        'Groepen hersteld. Start personages via deze launcher om de herstelde groepsleden te gebruiken.',
        'Восстановление завершено. Запускайте персонажей через этот лаунчер, чтобы использовать восстановленный состав групп.'),
    'Saved data changed. Scan again before restoring groups.': (
        '保存的数据已更改。恢复分组前请重新扫描。', '保存データが変更されました。復元前に再確認してください。',
        '저장된 데이터가 변경되었습니다. 그룹 복구 전에 다시 확인하세요.',
        'Les données sauvegardées ont changé. Relancez l’analyse avant de restaurer les groupes.',
        'Gespeicherte Daten wurden geändert. Vor der Wiederherstellung erneut prüfen.',
        'De opgeslagen gegevens zijn gewijzigd. Controleer opnieuw voordat je groepen herstelt.',
        'Сохранённые данные изменились. Повторите проверку перед восстановлением групп.'),
    'Recovery could not be completed. Existing profiles were kept.': (
        '无法完成恢复。原有配置已保留。', '復元を完了できませんでした。既存のプロファイルは保持されています。',
        '복구를 완료하지 못했습니다. 기존 프로필을 유지했습니다.',
        'La restauration n’a pas pu aboutir. Les profils existants ont été conservés.',
        'Wiederherstellung fehlgeschlagen. Vorhandene Profile bleiben erhalten.',
        'Het herstellen is niet gelukt. Bestaande profielen zijn behouden.',
        'Не удалось завершить восстановление. Существующие профили сохранены.'),
    'Select Shared settings and save your settings before recovering drone groups.': (
        '恢复无人机分组前，请选择共享设置并保存。', '復元前に共有設定を選択し、設定を保存してください。',
        '드론 그룹을 복구하기 전에 공유 설정을 선택하고 저장하세요.',
        'Sélectionnez les paramètres partagés et enregistrez-les avant de restaurer les groupes de drones.',
        'Vor der Wiederherstellung gemeinsame Einstellungen wählen und speichern.',
        'Kies Gedeelde instellingen en sla je instellingen op voordat je dronegroepen herstelt.',
        'Перед восстановлением групп выберите общие настройки и сохраните их.'),
    'Save or discard your settings changes before recovering drone groups.': (
        '恢复无人机分组前，请保存或放弃设置更改。', '復元前に設定の変更を保存するか破棄してください。',
        '드론 그룹을 복구하기 전에 설정 변경을 저장하거나 취소하세요.',
        'Enregistrez ou abandonnez vos modifications avant de restaurer les groupes de drones.',
        'Änderungen vor der Wiederherstellung speichern oder verwerfen.',
        'Sla je wijzigingen op of verwerp ze voordat je dronegroepen herstelt.',
        'Сохраните или отмените изменения настроек перед восстановлением групп дронов.'),
    'Drone recovery is unavailable for this runtime. It needs a verified local game database.': (
        '此运行环境无法恢复无人机分组。需要已验证的本地游戏数据库。',
        'この実行環境では復元できません。確認済みのローカルゲームデータベースが必要です。',
        '이 실행 환경에서는 드론 복구를 사용할 수 없습니다. 확인된 로컬 게임 데이터베이스가 필요합니다.',
        'La restauration des drones est indisponible pour cet environnement. Une base de jeu locale vérifiée est nécessaire.',
        'Drohnenwiederherstellung ist für diese Laufzeit nicht verfügbar. Eine überprüfte lokale Spieldatenbank wird benötigt.',
        'Droneherstel is niet beschikbaar voor deze omgeving. Er is een gecontroleerde lokale spelgegevensbank nodig.',
        'Восстановление дронов недоступно для этой среды. Нужна проверенная локальная база данных игры.'),
    'Some characters could not be checked; their profiles will be kept.': (
        '无法检查部分角色；其配置将保留。', '確認できなかったキャラクターのプロファイルは保持されます。',
        '일부 캐릭터를 확인하지 못했습니다. 해당 프로필은 유지됩니다.',
        'Certains personnages n’ont pas pu être vérifiés ; leurs profils seront conservés.',
        'Einige Charaktere konnten nicht geprüft werden; ihre Profile bleiben erhalten.',
        'Sommige personages konden niet worden gecontroleerd; hun profielen blijven behouden.',
        'Некоторых персонажей не удалось проверить; их профили останутся без изменений.'),
    'Recovery uses exact saved drone identities and current ownership. It keeps existing assignments and skips conflicting history.': (
        '恢复会核对保存的无人机标识和当前归属，保留现有分配，并跳过有冲突的历史记录。',
        '保存された正確なドローン ID と現在の所有者を照合します。現在の所属を保持し、矛盾する履歴は除外します。',
        '저장된 정확한 드론 ID와 현재 소유권을 대조합니다. 기존 구성을 유지하고 충돌하는 기록은 건너뜁니다.',
        'La restauration vérifie les identités exactes des drones et leur propriétaire actuel. Les affectations existantes sont conservées et les historiques contradictoires sont ignorés.',
        'Die Wiederherstellung prüft genaue gespeicherte Drohnen-IDs und aktuelle Eigentümer. Bestehende Zuordnungen bleiben erhalten; widersprüchliche Verläufe werden übersprungen.',
        'Herstel controleert de opgeslagen drone-ID’s en de huidige eigenaar. Bestaande indelingen blijven behouden; tegenstrijdige geschiedenis wordt overgeslagen.',
        'Восстановление сверяет точные сохранённые ID дронов с текущим владельцем. Существующие назначения сохраняются, противоречивые данные пропускаются.'),
    'This scan belongs to a different installation. Close it and scan again.': (
        '此扫描属于其他安装。请关闭并重新扫描。', 'この確認結果は別のインストール用です。閉じて再確認してください。',
        '이 확인 결과는 다른 설치에 속합니다. 닫고 다시 확인하세요.',
        'Cette analyse concerne une autre installation. Fermez-la et relancez-la.',
        'Diese Prüfung gehört zu einer anderen Installation. Schließen und erneut prüfen.',
        'Deze controle hoort bij een andere installatie. Sluit dit venster en controleer opnieuw.',
        'Эта проверка относится к другой установке. Закройте окно и повторите проверку.'),
    'Wait for the active operation to finish before recovering drone groups.': (
        '请等待当前操作完成后再恢复无人机分组。', '実行中の操作が完了してから復元してください。',
        '진행 중인 작업이 끝난 후 드론 그룹을 복구하세요.',
        'Attendez la fin de l’opération en cours avant de restaurer les groupes de drones.',
        'Vor der Wiederherstellung den laufenden Vorgang abwarten.',
        'Wacht tot de huidige bewerking klaar is voordat je dronegroepen herstelt.',
        'Дождитесь завершения текущей операции перед восстановлением групп дронов.'),
    'Close EVE clients before recovering drone groups.': (
        '恢复无人机分组前，请关闭 EVE 客户端。', 'ドローングループの復元前に EVE クライアントを終了してください。',
        '드론 그룹을 복구하기 전에 EVE 클라이언트를 닫으세요.',
        'Fermez les clients EVE avant de restaurer les groupes de drones.',
        'EVE-Clients vor der Wiederherstellung der Drohnengruppen schließen.',
        'Sluit EVE-clients voordat je dronegroepen herstelt.',
        'Закройте клиенты EVE перед восстановлением групп дронов.'),
}
SOURCE_PHRASES = tuple(_ROWS)
UI_PHRASES_BY_LANGUAGE = {
    code: {phrase: row[index] for phrase, row in _ROWS.items()}
    for index, code in enumerate(LANGUAGES)
}
