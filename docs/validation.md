# 初回検証記録

2026-09-12、Linux/Docker 29.8.0、Containerlab 0.79.0、cEOS 4.34.0Fの実コンテナで検証しました。Floci 2.0.1はmockを無効にし、CLIはRunTask、gNMIはdesiredCount=1のServiceとして実コンテナを実行しています。

| 検証 | 結果 |
|---|---|
| 6台のBGP | 16方向のセッションすべてEstablished |
| 外部ASプレフィックス | 192.0.2.1と198.51.100.1の相互疎通成功 |
| CLI/gNMI | 44 entityを両経路で保存。BGP・取得可能なIF状態が一致 |
| Prometheus | 6台同期、BGP 16、Ethernet 14、IFカウンター取得 |
| Grafana | 両データソースのhealth成功、両OpenSearchテーブルの実クエリ成功、画面表示確認 |
| OpenSearch Dashboards | API statusがgreen、Discover用の`observations-*` index patternを自動作成 |
| eBGP停止の反映 | 14.26秒（60秒以内） |
| eBGP復旧の反映 | 16.02秒（60秒以内） |
| 内部リンク断 | 状態変化を検出し、冗長経路で相互疎通成功 |
| gNMI切断 | 接続異常を表示し、古いBGPメトリクスを除去 |
| gNMI復旧 | 再接続・初期同期・監視再開 |
| ECS Task停止 | Serviceが代替Taskを起動し、discovery更新後に6台の監視再開 |
| 保存キュー | 最終検証時の破棄件数0 |
| CLI 1台接続不能 | edge1の接続先ポートをテスト内で変更。戻り値1、他5台の実収集・保存成功 |
| ユニットテスト | 18件成功、Ruffチェック・フォーマット成功 |

障害注入はすべて復旧済みです。Task置換後の最終正常系検証は2026-09-12 14:01:58 UTCに成功しました。停止・再起動でも観測履歴を保持する構成です。今回の通常停止・再起動でも保存データを保持できました。

CLIの接続不能試験はホストから同じCollectorモジュールを実行し、実際のSSHタイムアウトとOpenSearchへの保存を確認しています。正常系とリンク障害時のCLI収集はECS RunTaskです。JSON解析失敗、未取得値、部分更新・削除、保存再試行はfixtureとユニットテストで検証しています。

実装中に発見して修正した点は、pyGNMIのEOS初期通知処理、フィールドごとの時刻順序、leaf通知集中時の保存負荷、Floci TaskのDNS名、Task置換後のPrometheus準備完了判定、Grafana PPLのスクリプト生成上限です。版固有の制約は[設計メモ](design.md)を参照してください。

再実行は`make verify`、`make fault-test`、`make test`を使用します。機械可読の実行結果はGit対象外の`runtime/evidence/`に保存されます。上記の秒数はこのホストでの実測で、別環境で同一の遅延を保証するものではありません。

## Received prefix追加検証

2026-09-13、同じ互換構成でCollectorとGrafanaを再構築し、`make verify`を実行しました。IPv4-unicastのreceived prefix数は全16 BGP peerでgNMI Gaugeとして公開され、直前に実行したCLI収集の`prefixReceived`と一致しました。6台のstream同期、16セッション、14 Ethernet、両Grafanaデータソース、Deviceを含む両ダッシュボードのOpenSearchクエリも成功しました。`make test`は21件、Ruffのlintとformatチェックも成功しています。
