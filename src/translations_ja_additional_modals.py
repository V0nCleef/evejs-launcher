"""Reviewed Japanese translations for app modals and stable diagnostics.

This deliberately narrow catalog covers only the three matching sections in
``translations_source.py``: app-owned modal titles, app-owned modal bodies and
progress text, and stable launcher diagnostics.  Page, widget, wizard, and
updater copy belongs to their own catalogs.
"""
from __future__ import annotations


_ADDITIONAL_JA_MODAL_ITEMS: tuple[tuple[str, str], ...] = (
    # App-owned modal titles.
    ("Account Already Running", "アカウントは既に実行中"),
    ("Already Running", "既に実行中"),
    ("Apply Docker Mods", "Docker モッドを適用"),
    ("Character Created", "キャラクターを作成しました"),
    ("Character Created — Cleanup Unconfirmed", "キャラクター作成完了 — クリーンアップ未確認"),
    ("Character Created — Overview Not Queued", "キャラクター作成完了 — オーバービュー未予約"),
    ("Character Created — Services Not Restored", "キャラクター作成完了 — サービス未復元"),
    ("Character Groups", "キャラクターグループ"),
    ("Choose Server Start Script", "サーバー起動スクリプトを選択"),
    ("Clients Running", "クライアントが実行中"),
    ("Confirm EveJS Deletion", "EveJS の削除を確認"),
    ("Create Character Safely", "キャラクターを安全に作成"),
    ("Data Source Changed", "データソースが変更されました"),
    ("Delete Account Instead", "代わりにアカウントを削除"),
    ("Delete Character or Account", "キャラクターまたはアカウントを削除"),
    ("Deletion Cancelled", "削除をキャンセルしました"),
    ("Deletion Complete", "削除が完了しました"),
    ("Deletion Failed", "削除に失敗しました"),
    ("Docker Compose", "Docker Compose"),
    ("Docker Corrective Stop Failed", "Docker の修正停止に失敗しました"),
    ("Docker Lifecycle Failed", "Docker のライフサイクル処理に失敗しました"),
    ("Docker Mod Verification Failed", "Docker モッドの検証に失敗しました"),
    ("Docker Mods Failed", "Docker モッドの処理に失敗しました"),
    ("Docker Shutdown Failed", "Docker の停止に失敗しました"),
    ("Docker Tool Operation Failed", "Docker ツールの処理に失敗しました"),
    ("EVE Client", "EVE クライアント"),
    ("EVE Client Overview Patch", "EVE クライアントのオーバービューパッチ"),
    ("EVE Clients Running", "EVE クライアントが実行中"),
    ("External Game Server", "外部ゲームサーバー"),
    ("External Market Server", "外部マーケットサーバー"),
    ("Game Server", "ゲームサーバー"),
    ("Group Needs Attention", "グループの確認が必要です"),
    ("Groups Not Saved", "グループは保存されていません"),
    ("Invalid Configuration", "設定が無効です"),
    ("Invalid EVE Client Path", "EVE クライアントのパスが無効です"),
    ("Invalid EveJS Installation", "EveJS のインストールが無効です"),
    ("Invalid Server Mode", "サーバーモードが無効です"),
    ("Killed", "強制終了しました"),
    ("Launch Cancelled", "起動をキャンセルしました"),
    ("Launch Complete", "起動が完了しました"),
    ("Launch Error", "起動エラー"),
    ("Launch In Progress", "起動処理中"),
    ("Launcher Busy", "ランチャーは処理中です"),
    ("Market Server", "マーケットサーバー"),
    ("Mod Removal Busy", "モッドの削除処理中"),
    ("Mod Removal Failed", "モッドの削除に失敗しました"),
    ("Mod Removal Not Started", "モッドの削除を開始できませんでした"),
    ("Mod Removal Unavailable", "モッドを削除できません"),
    ("Mod Removed", "モッドを削除しました"),
    ("Mod Removed with Warning", "警告付きでモッドを削除しました"),
    ("Mod Restart Busy", "モッドの再起動処理中"),
    ("New Character", "新しいキャラクター"),
    ("Not Configured", "未設定"),
    ("Optional Market Not Ready", "任意の Market サービスは準備未完了です"),
    ("Overview Copy", "オーバービューをコピー"),
    ("Runtime Change In Progress", "実行環境を変更中"),
    ("Selection Changed", "選択内容が変更されました"),
    ("Service Restore", "サービスの復元"),
    ("Service Shutdown Failed", "サービスの停止に失敗しました"),
    ("Settings Deferred", "設定の適用を保留しました"),
    ("Stop External EveJS Services", "外部 EveJS サービスを停止"),
    ("Tool Launch Failed", "ツールの起動に失敗しました"),
    ("Tool Unavailable", "ツールを利用できません"),
    ("Type to Confirm", "入力して確認"),
    ("Unsaved Settings", "未保存の設定"),
    ("Unsupported Server Script", "未対応のサーバースクリプト"),
    ("Unsupported Tool Action", "未対応のツール操作"),
    ("Update Unavailable", "アップデートを利用できません"),

    # App-owned modal bodies and progress text.
    (
        "Account '{username}' is already running character '{character}'.",
        "アカウント「{username}」では、キャラクター「{character}」が既に実行中です。",
    ),
    (
        "An EveJS service is still running; no database changes were made.",
        "EveJS サービスがまだ実行中のため、データベースは変更されていません。",
    ),
    ("Another client is currently being prepared. Please wait.", "別のクライアントを準備しています。しばらくお待ちください。"),
    (
        "Another lifecycle, client-launch, maintenance, or update operation took ownership before removal could start. Nothing was removed.",
        "削除の開始前に、別のライフサイクル処理、クライアント起動、メンテナンス、またはアップデート処理が制御を取得しました。何も削除されていません。",
    ),
    (
        "Another server, maintenance, client-launch, or update operation is still running. Try again when it finishes.",
        "別のサーバー処理、メンテナンス、クライアント起動、またはアップデート処理が実行中です。完了してからもう一度お試しください。",
    ),
    (
        "Another service operation is still running. Try again when it finishes.",
        "別のサービス処理が実行中です。完了してからもう一度お試しください。",
    ),
    (
        "Any Game and Market servers started by this launcher will be stopped first and will stay stopped after removal.\n\nChoose what happens to this mod's local saved data. Shared EveJS or GameStore database records are not deleted.",
        "このランチャーが起動した Game サーバーと Market サーバーを先に停止し、削除後も停止したままにします。\n\nこのモッドのローカル保存データをどのように扱うか選択してください。共有の EveJS または GameStore データベースレコードは削除されません。",
    ),
    (
        "Apply the selected mod preload chain and recreate the server container?\n\nConnected clients will be disconnected.",
        "選択したモッドのプリロードチェーンを適用し、サーバーコンテナを再作成しますか？\n\n接続中のクライアントは切断されます。",
    ),
    ("Backing up, deleting, and verifying...", "バックアップ、削除、検証を実行しています..."),
    ("Both servers are already online.", "両方のサーバーは既にオンラインです。"),
    (
        "Character creation finished, but the prior EveJS services could not be restarted automatically.",
        "キャラクターの作成は完了しましたが、以前の EveJS サービスを自動的に再起動できませんでした。",
    ),
    ("Character groups could not be saved: {error}", "キャラクターグループを保存できませんでした: {error}"),
    (
        "Close every EVE client before deleting a character or account.",
        "キャラクターまたはアカウントを削除する前に、すべての EVE クライアントを閉じてください。",
    ),
    (
        "Configure the EveJS root and copied EVE client path first.",
        "先に EveJS ルートとコピー済み EVE クライアントのパスを設定してください。",
    ),
    (
        "Delete {subject}?\n\n{detail}\n\nEveJS will run its native character cleanup. The launcher will keep a recoverable backup of every affected table and portrait. Account profile/settings folders are preserved.{service_note}",
        "{subject}を削除しますか？\n\n{detail}\n\nEveJS の標準キャラクタークリーンアップを実行します。ランチャーは影響を受けるすべてのテーブルとポートレートについて、復元可能なバックアップを保持します。アカウントのプロファイルおよび設定フォルダーは維持されます。{service_note}",
    ),
    (
        "Deletion finished, but the prior EveJS services could not be restarted automatically.",
        "削除は完了しましたが、以前の EveJS サービスを自動的に再起動できませんでした。",
    ),
    ("Docker lifecycle operation failed.", "Docker のライフサイクル処理に失敗しました。"),
    (
        "Docker shutdown could not be confirmed. The launcher remains open; check Docker status and retry.",
        "Docker の停止を確認できませんでした。ランチャーは開いたままです。Docker の状態を確認して、もう一度お試しください。",
    ),
    ("Docker tool operation failed.", "Docker ツールの処理に失敗しました。"),
    (
        "Do not retry creation. EveJS did not confirm final maintenance cleanup, so the Compose services were kept stopped. Retain the scoped backup and verify the game store before starting services.",
        "作成を再試行しないでください。EveJS が最終メンテナンスのクリーンアップを確認できなかったため、Compose サービスは停止したままです。対象範囲のバックアップを保持し、サービスを開始する前にゲームストアを検証してください。",
    ),
    ("EveJS Deletion", "EveJS の削除"),
    ("EveJS root or client path not set.", "EveJS ルートまたはクライアントのパスが設定されていません。"),
    (
        "Game is online and remains usable without the optional Market service. Use the Market Console button on Home for details.",
        "Game はオンラインで、任意の Market サービスがなくても利用できます。詳細はホームの Market コンソールボタンを使用してください。",
    ),
    (
        "Game reached its endpoint, but the launcher could not prove the requested mod state. The Game server is being stopped gracefully and will not be left running in an unknown state.\n\n{diagnostics}",
        "Game はエンドポイントに到達しましたが、ランチャーは要求されたモッド状態を確認できませんでした。Game サーバーを正常に停止しており、不明な状態のまま実行されることはありません。\n\n{diagnostics}",
    ),
    (
        "Launcher-managed deletion is currently available for Native EveJS installations only.",
        "ランチャーによる削除は、現在 Native の EveJS インストールでのみ利用できます。",
    ),
    (
        "Launcher-managed Docker character creation requires Managed Docker mode. Connect-only mode remains read-only.",
        "ランチャーによる Docker キャラクター作成には Managed Docker モードが必要です。接続専用モードは読み取り専用のままです。",
    ),
    (
        "No authorized Docker command consumed the new override. Its exact prior state was restored.",
        "承認された Docker コマンドは新しいオーバーライドを使用しませんでした。以前の正確な状態を復元しました。",
    ),
    (
        "No StartServer*.bat indicator was found, and the legacy server/index.js entry point is missing.",
        "StartServer*.bat のインジケーターが見つからず、従来の server/index.js エントリーポイントもありません。",
    ),
    (
        "One or more group memberships could not be saved; open Manage Groups to remove missing entries.",
        "1 件以上のグループ所属を保存できませんでした。［グループを管理］を開き、見つからない項目を削除してください。",
    ),
    ("Preparing a recoverable backup...", "復元可能なバックアップを準備しています..."),
    ("Remove & Keep Data", "削除してデータを保持"),
    ("Remove & Quarantine Local Data", "削除してローカルデータを隔離"),
    ("Remove {display_name}", "{display_name}を削除"),
    (
        "Remove {display_name} {package_version} from this EveJS server?",
        "この EveJS サーバーから {display_name} {package_version} を削除しますか？",
    ),
    (
        "Select the copied EVE client tq folder in Settings. It must contain start.ini and bin64\\exefile.exe.",
        "設定でコピー済み EVE クライアントの tq フォルダーを選択してください。start.ini と bin64\\exefile.exe が含まれている必要があります。",
    ),
    ("Select the server mode indicator for this start:", "今回の起動に使用するサーバーモードのインジケーターを選択してください:"),
    ("Set up EveJS first.", "先に EveJS を設定してください。"),
    ("Set up EveJS root in Settings first.", "先に設定で EveJS ルートを指定してください。"),
    (
        "Runtime target settings cannot be applied while a server lifecycle is in progress. Try Save again when it finishes.",
        "サーバーのライフサイクル処理中は、実行環境の対象設定を適用できません。処理が完了してから［保存］をもう一度お試しください。",
    ),
    (
        "Switch to the Native backend before removing an installed mod.",
        "インストール済みのモッドを削除する前に Native バックエンドへ切り替えてください。",
    ),
    ("Terminated {count} client(s).", "{count} 件のクライアントを終了しました。"),
    (
        "The active EveJS data source changed while groups were open. Reopen Manage Groups and try again.",
        "グループ画面を開いている間に、使用中の EveJS データソースが変更されました。［グループを管理］を開き直して、もう一度お試しください。",
    ),
    (
        "The background removal worker could not start. Nothing was removed.\n\n{error}",
        "バックグラウンドの削除ワーカーを開始できませんでした。何も削除されていません。\n\n{error}",
    ),
    ("The client launch worker could not start.", "クライアント起動ワーカーを開始できませんでした。"),
    ("The confirmation text did not match. No data was changed.", "確認用の文字列が一致しません。データは変更されていません。"),
    ("The Docker account and character were created and verified.", "Docker のアカウントとキャラクターを作成し、検証しました。"),
    (
        "The Docker mod preload configuration could not be frozen and updated safely. The server container was not recreated.\n\n{error}{rollback_failure}",
        "Docker モッドのプリロード設定を安全に固定して更新できませんでした。サーバーコンテナは再作成されていません。\n\n{error}{rollback_failure}",
    ),
    (
        "The EveJS services could not be prepared safely. No database changes were made.",
        "EveJS サービスを安全に準備できませんでした。データベースは変更されていません。",
    ),
    (
        "The Game server is being stopped so it is not left running with an unknown mod state.",
        "モッド状態が不明なまま実行されないように、Game サーバーを停止しています。",
    ),
    (
        "The Game server's mod state is unverified and Docker did not confirm that the container stopped. Check Docker immediately.",
        "Game サーバーのモッド状態は未検証で、Docker でもコンテナの停止を確認できませんでした。直ちに Docker を確認してください。",
    ),
    (
        "The game server was started outside this launcher. Stop it from its original console before deleting data.",
        "ゲームサーバーはこのランチャー以外から起動されています。データを削除する前に、起動元のコンソールから停止してください。",
    ),
    (
        "The game server was started outside this launcher.\n\nStop it from its original console before starting a replacement through this launcher.",
        "ゲームサーバーはこのランチャー以外から起動されています。\n\nこのランチャーから代わりのサーバーを起動する前に、起動元のコンソールから停止してください。",
    ),
    (
        "The game server was started outside this launcher.\n\nStop it from its original console, then restart it through this launcher if you want the launcher to manage it.",
        "ゲームサーバーはこのランチャー以外から起動されています。\n\nランチャーで管理する場合は、起動元のコンソールから停止し、このランチャーで再起動してください。",
    ),
    (
        "The installed mod state could not be validated before Game startup. No Game process was started.\n\n{error}",
        "Game の起動前に、インストール済みモッドの状態を検証できませんでした。Game プロセスは開始されていません。\n\n{error}",
    ),
    (
        "The launcher attempted automatic rollback; no unverified deletion was accepted.",
        "ランチャーは自動ロールバックを試行し、未検証の削除は受け入れませんでした。",
    ),
    ("The launcher could not reserve the server lifecycle. Nothing was removed.", "ランチャーはサーバーのライフサイクルを確保できませんでした。何も削除されていません。"),
    (
        "The launcher could not start the corrective Game stop. Check Docker state before allowing clients to reconnect.{worker_error}",
        "ランチャーは Game の修正停止を開始できませんでした。クライアントの再接続を許可する前に Docker の状態を確認してください。{worker_error}",
    ),
    (
        "The launcher could not verify this mod's removal kit. Nothing was removed.\n\n{error}",
        "ランチャーはこのモッドの削除キットを検証できませんでした。何も削除されていません。\n\n{error}",
    ),
    (
        "The launcher will temporarily stop its EveJS services, back up the affected game-store tables, create and verify the character, then restore the previous service state. Continue?",
        "ランチャーは EveJS サービスを一時停止し、影響を受けるゲームストアのテーブルをバックアップしてから、キャラクターの作成と検証を行い、以前のサービス状態を復元します。続行しますか？",
    ),
    (
        "The launcher will temporarily stop the selected Compose services, create a scoped game-store backup, create and verify the character, then restore only the services that were online. Continue?",
        "ランチャーは選択した Compose サービスを一時停止し、対象範囲のゲームストアバックアップを作成してから、キャラクターの作成と検証を行い、オンラインだったサービスだけを復元します。続行しますか？",
    ),
    (
        "The market server was started outside this launcher. Stop it before deleting data.",
        "マーケットサーバーはこのランチャー以外から起動されています。データを削除する前に停止してください。",
    ),
    (
        "The market server was started outside this launcher.\n\nClose it manually via Task Manager, or stop the game server\nand restart both through the launcher.",
        "マーケットサーバーはこのランチャー以外から起動されています。\n\nタスクマネージャーから手動で閉じるか、ゲームサーバーを停止してから、\n両方をランチャーで再起動してください。",
    ),
    ("The pending overview import could not be saved: {error}", "保留中のオーバービューインポートを保存できませんでした: {error}"),
    ("The prior Compose service state could not be restored automatically.", "以前の Compose サービス状態を自動的に復元できませんでした。"),
    ("The registered mod uninstaller failed.", "登録済みのモッドアンインストーラーが失敗しました。"),
    (
        "The removal worker returned an invalid result. Refresh Mods before retrying.",
        "削除ワーカーが無効な結果を返しました。再試行する前にモッドを更新してください。",
    ),
    ("The selected account or character changed. Refresh and try again.", "選択したアカウントまたはキャラクターが変更されました。更新して、もう一度お試しください。"),
    ("The selected group could not be saved: {error}", "選択したグループを保存できませんでした: {error}"),
    (
        "The server shutdown sequence could not be started. Nothing was removed.\n\n{error}",
        "サーバー停止処理を開始できませんでした。何も削除されていません。\n\n{error}",
    ),
    ("The service startup worker could not be started.", "サービス起動ワーカーを開始できませんでした。"),
    (
        "The {service_label} {service_noun} reachable again. Nothing was removed.\n\nStop the live service from its original console, then retry Remove. The launcher will not alter files underneath a running server.",
        "{service_label} {service_noun} に再び接続できる状態です。何も削除されていません。\n\n実行中のサービスを起動元のコンソールから停止し、削除を再試行してください。ランチャーは実行中のサーバー配下のファイルを変更しません。",
    ),
    (
        "The {service_label} {service_noun} started outside this launcher.\n\nStop {service_pronoun} from the original console, then remove the mod from the Mods page. The launcher will not alter live files underneath a server it does not own.",
        "{service_label} {service_noun} はこのランチャー以外から起動されています。\n\n起動元のコンソールから {service_pronoun} を停止し、モッドページからモッドを削除してください。ランチャーは管理していない実行中サーバー配下のファイルを変更しません。",
    ),
    ("This release does not include a downloadable launcher package.", "このリリースにはダウンロード可能なランチャーパッケージが含まれていません。"),
    ("Type exactly:\n{expected}", "次の文字列を正確に入力してください:\n{expected}"),
    ("Unsupported legacy server mode: {mode}", "未対応の従来型サーバーモードです: {mode}"),
    ("Use the Game Console or Market Console button on Home for details.", "詳細はホームの Game コンソールまたは Market コンソールボタンを使用してください。"),
    (
        "Wait for the active server or mod operation to finish before launching an EVE client.",
        "EVE クライアントを起動する前に、実行中のサーバーまたはモッド処理が完了するまでお待ちください。",
    ),
    (
        "Wait for the active server or mod operation to finish before launching EVE clients.",
        "EVE クライアントを起動する前に、実行中のサーバーまたはモッド処理が完了するまでお待ちください。",
    ),
    (
        "Wait for the current character launch queue to finish before editing groups.",
        "グループを編集する前に、現在のキャラクター起動キューが完了するまでお待ちください。",
    ),
    ("Wait for the current EveJS character data to finish loading.", "現在の EveJS キャラクターデータの読み込みが完了するまでお待ちください。"),
    ("Wait for the current launcher operation to finish.", "現在のランチャー処理が完了するまでお待ちください。"),
    (
        "Wait for the launcher to verify the selected Docker Compose project, then try again.",
        "ランチャーが選択した Docker Compose プロジェクトを検証するまで待ってから、もう一度お試しください。",
    ),
    ("You have unsaved Settings changes. Save them before leaving?", "設定に未保存の変更があります。移動する前に保存しますか？"),
    (
        "'{character}' is the only character on '{username}'.\n\nUse Delete Account so the empty account does not become inaccessible.",
        "「{character}」は「{username}」に属する唯一のキャラクターです。\n\n空のアカウントにアクセスできなくならないよう、［アカウントを削除］を使用してください。",
    ),
    ("{running} client(s) still running.\nKill them and exit?", "{running} 件のクライアントがまだ実行中です。\nすべて終了してランチャーを閉じますか？"),

    # Stable launcher diagnostics surfaced as status/help text.
    ("A positive character ID is required for automatic login.", "自動ログインには正のキャラクター ID が必要です。"),
    ("Automatic login is restricted to a local EveJS game endpoint.", "自動ログインはローカル EveJS ゲームエンドポイントでのみ利用できます。"),
    ("Close every EVE client before patching code.ccp.", "code.ccp をパッチする前に、すべての EVE クライアントを閉じてください。"),
    ("Close every EVE client before restoring code.ccp.", "code.ccp を復元する前に、すべての EVE クライアントを閉じてください。"),
    (
        "Compose configuration is invalid. Check the selected Compose file and its local paths.",
        "Compose の設定が無効です。選択した Compose ファイルとローカルパスを確認してください。",
    ),
    (
        "Compose configuration must define the required EveJS server and market services.",
        "Compose の設定には、必要な EveJS サーバーおよびマーケットサービスを定義する必要があります。",
    ),
    (
        "Docker CLI was not found. Install Docker Desktop or add docker.exe to PATH.",
        "Docker CLI が見つかりません。Docker Desktop をインストールするか、docker.exe を PATH に追加してください。",
    ),
    (
        "Docker Compose plugin is unavailable. Install or enable Docker Compose in Docker Desktop.",
        "Docker Compose プラグインを利用できません。Docker Desktop で Docker Compose をインストールまたは有効化してください。",
    ),
    (
        "Docker Compose service status could not be inspected. Check Docker Desktop and the selected project.",
        "Docker Compose サービスの状態を確認できませんでした。Docker Desktop と選択したプロジェクトを確認してください。",
    ),
    (
        "Docker Desktop engine is unavailable. Start Docker Desktop and wait for the engine to finish starting.",
        "Docker Desktop エンジンを利用できません。Docker Desktop を起動し、エンジンの起動が完了するまでお待ちください。",
    ),
    (
        "Docker is using Windows containers. Switch Docker Desktop to Linux containers.",
        "Docker は Windows コンテナを使用しています。Docker Desktop を Linux コンテナへ切り替えてください。",
    ),
    ("EVE build {build} is not supported by the overview bridge.", "EVE ビルド {build} はオーバービューブリッジでサポートされていません。"),
    ("EVE build {build} is not supported for automatic login.", "EVE ビルド {build} は自動ログインに対応していません。"),
    ("EveJS local password bypass is disabled or could not be verified.", "EveJS のローカルパスワード回避が無効か、検証できませんでした。"),
    (
        "Legacy overview bridge v1/v2 detected. Restore the original, then install the current bridge.",
        "従来のオーバービューブリッジ v1/v2 を検出しました。オリジナルを復元してから、現在のブリッジをインストールしてください。",
    ),
    ("Overview copy bridge v3 installed; original backup verified.", "オーバービューコピーブリッジ v3 がインストールされ、オリジナルのバックアップも検証済みです。"),
    ("Select a valid EveJS root.", "有効な EveJS ルートを選択してください。"),
    ("Select the copied EVE client tq folder.", "コピー済み EVE クライアントの tq フォルダーを選択してください。"),
    ("Select the copied EVE client tq folder first.", "先にコピー済み EVE クライアントの tq フォルダーを選択してください。"),
    ("Supported — copied EVE build 3396210; no client patch required.", "対応済み — コピー済み EVE ビルド 3396210。クライアントパッチは不要です。"),
    (
        "Supported EVE build 3396210; ready to install the optional overview bridge.",
        "対応する EVE ビルド 3396210 です。任意のオーバービューブリッジをインストールできます。",
    ),
    ("The account username is not safe for automatic login.", "このアカウントのユーザー名は自動ログインに安全に使用できません。"),
    ("The build 3396210 login modules are missing or modified.", "ビルド 3396210 のログインモジュールが見つからないか、変更されています。"),
    ("The copied client build could not be identified.", "コピー済みクライアントのビルドを特定できませんでした。"),
    ("The copied client does not expose the required no-console mode.", "コピー済みクライアントは、必要なコンソールなしモードを提供していません。"),
    (
        "This code.ccp is modified or does not match the proven build 3396210 archive.",
        "この code.ccp は変更されているか、検証済みのビルド 3396210 アーカイブと一致しません。",
    ),
)


ADDITIONAL_JA_MODAL_PHRASES: dict[str, str] = dict(_ADDITIONAL_JA_MODAL_ITEMS)

if len(ADDITIONAL_JA_MODAL_PHRASES) != len(_ADDITIONAL_JA_MODAL_ITEMS):
    raise RuntimeError("Duplicate Japanese modal translation source key")


__all__ = ("ADDITIONAL_JA_MODAL_PHRASES",)
