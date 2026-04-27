# saiverse-x-addon

SAIVerse のペルソナを X (Twitter) に接続し、投稿・取得・反応・定期ポーリング通知を可能にするアドオン。

## 提供するもの

- **OAuth 2.0 PKCE 認可フロー** — ペルソナごとに X アカウントを連携 (アドオン管理 UI から接続)
- **14 個のスペル** — 投稿、リプライ、いいね、リツイート、フォロー、検索、ユーザー情報、メンション/フォロワー/被いいね/被リポスト確認 など
- **定期ポーリング統合** — メンション・新規フォロワー・被いいね/被リポスト件数・誰がいいね/リポストしたか の検出 (各項目 ON/OFF 可能)
- **通知 Playbook** (`x_poll_handler`) — ポーリング結果をペルソナに `<system>` メッセージで通知し、対応はペルソナの自由判断
- **アドオン専用 SQLite (`x_reply_log.db`)** — 二重リプライ防止 (X 規約遵守)

## 提供スペル一覧

メイン (`spell_visible=True`):
- `x_post_tweet` — Xに投稿
- `x_read_mentions` — Xメンションを見る (新着以外も全件取得)
- `x_read_timeline` — Xホームタイムラインを見る
- `x_search_tweets` — Xを検索
- `x_check_mentions` — **前回以降の**新規メンション (ポーリングと since 共有)
- `x_check_new_followers` — 新規フォロワー差分
- `x_check_post_likes` — 自分の最新ポストの被いいね差分 (`with_users=true` で誰が も取得)
- `x_check_post_retweets` — 同上、被リポスト

詳細 (`spell_visible=False`、`addon_spell_help saiverse-x-addon` で発見):
- `x_reply_tweet` — Xにリプライ (二重リプ防止付き)
- `x_like_tweet` / `x_unlike_tweet`
- `x_retweet` / `x_unretweet`
- `x_delete_tweet` — 自分のツイート削除 (常に確認ダイアログ)
- `x_follow_user` / `x_unfollow_user`
- `x_get_user` — ユーザープロフィール取得
- `x_get_user_tweets` — 特定ユーザーのツイート取得

すべて X アカウント連携済みのペルソナにのみ表示されます (`ToolSchema.availability_check` 機構)。

## 前提条件

- **X Developer App** が必要 (Client ID / Client Secret を取得)。X API は 2026 年現在、従量課金制 ([公式 pricing](https://docs.x.com/x-api/getting-started/pricing))
- **Python 環境** に `requests`, `httpx`, `sqlalchemy` (SAIVerse 本体の依存に含まれる)

## セットアップ

### 1. X Developer App を作成

1. https://developer.x.com にアクセス、開発者アカウントを作成
2. **Project & App** を作成
3. **User authentication settings** で OAuth 2.0 を有効化:
   - **App permissions**: `Read and write` (投稿したい場合) / `Read and write and Direct message` (DMも使う場合)
   - **Type of App**: `Confidential client`
   - **Callback URI**: `http://127.0.0.1:8000/api/oauth/callback/saiverse-x-addon/x` (city_a の場合。city_b は port 9000)
   - **Website URL**: 任意 (ローカル運用なら `http://127.0.0.1:3000` など)
4. **Keys and tokens** タブから `OAuth 2.0 Client ID` と `Client Secret` を控える

### 2. SAIVerse でアドオンを有効化 + Client ID/Secret を設定

1. SAIVerse を起動
2. アドオン管理モーダルを開く
3. 「X (Twitter) 連携」アドオンを有効化 (トグル ON)
4. パラメータに `client_id` と `client_secret` を入力 (グローバル設定、全ペルソナ共有)

### 3. ペルソナごとに OAuth 認可

1. アドオン管理モーダル内の「X アカウント連携」セクションで、ペルソナを選択
2. 「接続する」ボタンを押す → ポップアップで X の認可ページが開く
3. 「Authorize app」をクリック
4. 自動的にウィンドウが閉じ、`@username` が表示されれば連携成功

複数ペルソナを連携する場合はペルソナごとに繰り返し。

### 4. ポーリング設定 (任意)

アドオン管理 UI のグローバル設定で、必要なポーリング項目だけ ON にする:

| 項目 | デフォルト | 備考 |
|---|---|---|
| `polling_enabled` | true | マスタートグル |
| `polling_interval_seconds` | 86400 (24h) | 自由入力可、最低 60 秒 |
| `poll_mentions` | true | リプライ・メンション |
| `mentions_max_results` | 20 | 1回あたりの最大件数 |
| `poll_followers` | false | 新規フォロワー (差分検出。初回はベースライン保存のみ) |
| `poll_engagement_count` | false | 被いいね/リポスト **件数のみ** (1リクエストで両方取得、低コスト) |
| `poll_likes_detail` | false | **誰が** いいねしたか (件数増えたツイートだけ liking_users 追加コール) |
| `poll_retweets_detail` | false | **誰が** リポストしたか (同上 retweeted_by) |
| `tracked_recent_posts_count` | 5 | likes/retweets 監視対象の最新ポスト数 |

ポーリングはコストに直結します。X API 従量課金単価は [公式 pricing](https://docs.x.com/x-api/getting-started/pricing) を参照。デフォルトは 24 時間に 1 回の最低限ポーリング。

### 5. ポーリング検出時の挙動

ポーリングで何か検出されると:
1. `TriggerType.X_POLL_DETECTED` のイベントが 1 回発火 (種類別に分けず、1ポーリング = 1 イベントに集約)
2. `x_poll_handler` Playbook が起動し、検出内容を `<system>` メッセージとしてペルソナの会話履歴に挿入
3. ペルソナは次の pulse でメッセージを読み、対応するか判断 (返信したいメンションには `x_reply_tweet` 等のスペルで個別対応)

## アーキテクチャメモ

- **資格情報の保存先**: 旧ファイルベース (`~/.saiverse/personas/<id>/x_credentials.json`) は廃止。`AddonPersonaConfig` テーブルに OAuth トークン (`x_access_token` / `x_refresh_token` / `x_token_expires_at` / `x_user_id` / `x_username`) を保存
- **トークンリフレッシュ**: Pull 型。`x_lib/credentials.load_credentials(persona_id)` 呼び出し時に期限切れなら `saiverse.oauth.handler.get_valid_token()` 経由で自動リフレッシュ
- **ポーリング状態**: `~/.saiverse/addons/saiverse-x-addon/poll_state/<persona_id>.json` (mentions の since_id、followers の known_ids、engagement の tweet_metrics スナップショット)
- **重複リプライ防止**: `~/.saiverse/addons/saiverse-x-addon/x_reply_log.db` (UNIQUE 制約)
- **MCP サーバー連携**: なし。すべてネイティブ Python 実装

## トラブルシュート

### OAuth 認可で「Callback URI mismatch」エラー

X Developer App の Callback URI 設定が `http://127.0.0.1:<api_port>/api/oauth/callback/saiverse-x-addon/x` と完全一致している必要があります。`localhost` ではなく `127.0.0.1` にすること、末尾スラッシュ無しに揃えること。

### スペルが表示されない

そのペルソナで X アカウント未連携の可能性。アドオン管理 UI の「X アカウント連携」で接続済みか確認。`addon_spell_help saiverse-x-addon` スペルで連携済みペルソナのスペル一覧を確認できます。

### ポーリングが走らない

- アドオンが有効化されているか (`polling_enabled` だけ ON でアドオン無効だと走らない)
- `polling_interval_seconds` が長すぎないか
- 連携済みペルソナが 0 だとスキップされる
- ログ: `~/.saiverse/user_data/logs/<session>/backend.log` を `XPolling` でgrep

### トークンリフレッシュが失敗する

`x_refresh_token` が無効化されている可能性。アドオン管理 UI から該当ペルソナを「切断」→「再接続」で OAuth フローをやり直す。

## ライセンス

Apache License 2.0 — 詳細は [LICENSE](./LICENSE) を参照。

## 参考

- X API v2 公式: https://docs.x.com/x-api
- X API pricing: https://docs.x.com/x-api/getting-started/pricing
- SAIVerse アドオン拡張点: `docs/intent/addon_extension_points.md` (本体リポジトリ内)
- SAIVerse 本体: https://github.com/maha0525/SAIVerse
