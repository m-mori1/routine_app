# Playwright E2E 実行手順（routine_app）

## 1. 前提
- Node.js 18 以上
- `https://mercury/routine_app/` へアクセス可能
- ログイン済みブラウザから取得した `session` Cookie

## 2. 初回セットアップ
```powershell
cd \\mercury\asp\routine_app
npm install
npx playwright install chromium
```

## 3. 環境変数設定
```powershell
$env:ROUTINE_BASE_URL = "https://mercury/routine_app/"
$env:ROUTINE_SESSION_COOKIE = "<session_cookie>"
```

## 4. 実行
```powershell
npm run e2e:test
```

画面を見ながら実行する場合:
```powershell
npm run e2e:test:headed
```

## 5. テスト内容
`tests/e2e/routine.spec.js` の1ケースで以下を実施します。
- 登録（開始月 `2026-02`、月次）
- 一覧確認
- 親更新
- 子更新
- 子完了
- 親完了

## 6. 注意
- このテストは本番同等環境のデータを更新します。
- 失敗時は `playwright-report/` と `test-results/` を確認してください。
