# Observation設計と実装上の境界

## 共通Schema

`src/observability/schema.py`が正本です。Pydanticモデルは`schema_version=1.0`、観測種別、device.id/name、observed_at、collected_at、source、data、deletedを持ちます。

- Interfaceのキー：device.id + interface。
- BGPのキー：device.id + VRF + peer + AFI/SAFI。
- transportはentity identityに含めず、OpenSearch上で別文書として保存します。
- CLIのobserved_atは応答受信時刻、gNMIは装置通知のタイムスタンプ。`source.timestamp_origin`で区別します。
- `collected_at`は正規化時刻。gNMIの部分更新から組み立てたpayloadは、その通知までに判明した状態です。payload全フィールドが同時に装置から送られたことは意味しません。
- nullは未取得。削除は`deleted=true`の履歴として保存し、ライブメトリクスから除去します。

## EOS 4.34.0Fの実測

`tests/fixtures/`は本ラボのedge1から取得したCLI JSONとgNMI Get応答です。startup-configや認証情報は含みません。`uv run python scripts/probe.py`でラボから再採取できます（fixtureを更新します）。

実機で確認したgNMIパス：

```
/interfaces/interface/state
/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state
/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/afi-safis/afi-safi/state/prefixes/received
```

JSON出力のキーにはOpenConfig名前空間が付くため、正規化時に除去します。Loopback0/1はIF名等を返す一方、admin/oper/countersを返しません。必須パスの同期検証はEthernetの状態とカウンター、BGP session-stateを対象にします。Loopbackの状態一致は検証から除外し、値を推定しません。

OpenConfigのinterface MTUはこのバージョンで0を返します。意味あるMTUとして扱わずnullとします。CLIのMTUとは比較しません。BGP neighbor/stateにないlocal-asはgNMI側で補完せずnullです。received prefix数はAFI/SAFI配下の専用パスから取得し、neighbor stateとleaf単位の時刻・実パスを保ったまま同じObservationへ統合します。v1のAFI/SAFIは、IPv4だけを有効化した本ラボの契約に従いipv4-unicastです。他AFIは受け入れず、Dual Stack対応時にentity identityを保ったAFI/SAFI別購読を追加します。

cEOSの仮想カウンターは実機ASICの転送性能や全トラフィックを再現する保証がありません。PoCでは状態遷移と時系列取得経路を検証します。

## ライフサイクル

Containerlabは固定ノードとリンクを管理し、FlociはCollectorの実Dockerコンテナを管理します。Docker-in-Dockerは使用せず、同じホストのDocker daemonを使います。ECSネットワーク設定でTaskをobs-mgmtへ接続します。

外部ASはGoBGP 4.5.0を埋め込んだAlpineコンテナ16台で、観測inventoryには含めません。external1〜8はedge1、external9〜16はedge2へ直結し、AS65101〜65116として固有の`/20` poolから`/32`を広告します。広告数はUTC時刻に同期した10分周期の正弦波（基準1,000、振幅300、30秒更新）で変化し、各edge内では8位相を均等配置します。経路はGoBGPのGlobal RIBだけに保持し、Linux kernel RIBやdata-plane到達性は外部経路の検証対象にしません。

変動中のCLIとgNMIは同じ瞬間のsnapshotではないため、session stateとremote ASは従来どおり完全一致を検証し、received prefix数は各transportで700〜1,300の範囲を個別に検証します。gNMI Gaugeについては60秒以内の値変化と、その間のsession継続を正常系の受入条件とします。

discovery helperは5秒ごとにECS ListTasks/DescribeTasksからgNMI Taskを取得し、Prometheus file discoveryをatomic replaceします。Taskがなくなった場合やAPI取得失敗時はターゲットを空にします。Prometheusは5秒周期でこのファイルを再読込します。Floci 2.0.1のDescribeTasksはENI/IPを返さないため、この版ではTask ARNとcontainer名からFlociのDocker DNS名を導きます。DNSラベル長を63文字以内に収めるため、リソースnamespaceはobsに固定しています。ENI情報が返る環境ではそのIPを優先します。

up/downの制御状態は再作成可能とし、FlociのAWSリソースはup時に再登録します。OpenSearch/Prometheus/Grafanaのデータだけをruntimeへ永続化します。downはServiceを0台にして削除し、Task停止を確認してから管理ネットワークを破棄します。

GrafanaのPPLテーブルは更新ごとに時刻フィルターを生成します。10秒更新で既定のコンパイル上限（75/5m）を超えるため、up時に`script.context.filter.max_compilations_rate=300/5m`を設定します。この値は本ラボのダッシュボードと検証用で、同時閲覧数を増やす場合は負荷と上限を再評価します。[OpenSearchのスクリプト設定](https://docs.opensearch.org/latest/install-and-configure/configuring-opensearch/script-and-resource-settings/)

## 公式資料

- [Containerlab cEOS](https://containerlab.dev/manual/kinds/ceos/)
- [Floci ECS](https://floci.io/floci/services/ecs/)
- [pyGNMI](https://github.com/akarneliuk/pygnmi)
- [scrapli](https://github.com/carlmontanari/scrapli)
- [Grafana OpenSearch datasource](https://github.com/grafana/opensearch-datasource)

## STREAMデコードの互換処理

pyGNMI 0.8.15の`subscribe2`は初期通知を結合して最後のprefixを共用し、EOSが返すTypedValueのleaf-listも処理に失敗します。そのため、pyGNMIのraw `subscribe` RPCを使用し、`wire.py`で通知単位のprefix、TypedValue、delete、sync_responseを復元します。ライブラリ本体へのパッチは不要です。

EOSは初期同期で異なるフィールドの最終更新時刻を返すため、順序判定はentity全体ではなくフィールド単位で行います。削除時刻も記録し、古い通知で削除済みデータが復活することを防ぎます。`gnmi-wire.json`と`gnmi-stream.json`は実際の初期同期98通知のfixtureです。
