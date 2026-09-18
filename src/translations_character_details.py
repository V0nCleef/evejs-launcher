"""Localized stat headings and explanations; game names use the SDE catalog."""

_KEYS = ("balance", "skill_points", "security_hint", "location_hint")
_VALUES = {
    "en": ("Balance", "Skill points", "Your character's personal security rating. System security is shown beside Location.", "Current solar system and its security level."),
    "zh_CN": ("余额", "技能点", "角色的个人安全等级。星系安全等级显示在位置旁。", "当前星系及其安全等级。"),
    "ja": ("残高", "スキルポイント", "キャラクター個人のセキュリティステータス。星系のセキュリティは場所の横に表示されます。", "現在のソーラーシステムとそのセキュリティレベル。"),
    "ko": ("잔액", "스킬 포인트", "캐릭터 개인의 보안 상태입니다. 성계 보안 등급은 위치 옆에 표시됩니다.", "현재 성계와 보안 등급입니다."),
    "fr": ("Solde", "Points de compétence", "Statut de sécurité personnel du personnage. La sécurité du système apparaît à côté de l’emplacement.", "Système solaire actuel et son niveau de sécurité."),
    "de": ("Guthaben", "Fertigkeitspunkte", "Persönlicher Sicherheitsstatus des Charakters. Die Systemsicherheit steht neben dem Standort.", "Aktuelles Sonnensystem und dessen Sicherheitsstufe."),
    "nl": ("Saldo", "Vaardigheidspunten", "De persoonlijke beveiligingsstatus van je personage. De systeembeveiliging staat naast de locatie.", "Het huidige zonnestelsel en het beveiligingsniveau."),
    "ru": ("Баланс", "Очки навыков", "Личный статус безопасности персонажа. Уровень безопасности системы указан рядом с местоположением.", "Текущая звёздная система и её уровень безопасности."),
}
TRANSLATIONS = {language: {f"character.{key}": (f"{value} (ISK)" if key == "balance" else value)
                           for key, value in zip(_KEYS, values, strict=True)}
                for language, values in _VALUES.items()}
