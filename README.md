# 概要

MT5にMCPの機能が追加されてAIから簡単に利用できるようになりました

MCPを使うと

- MT5からチャートの情報を取得できる
- 売買をMT5から取得できる
- チャートの情報を取得できるのでAIに分析してもらえる

と言いたメリットがあります。

今回は以下の流れを想定しています。

1. MCP経由でMT5からチャートの情報を取得
2. こちらで用意したPythonを利用してテクニカルチャートの計算を行う
3. AIで操作を行う

- 売買を行う
- Slack, Lineにメッセージを送信する

実行環境は以下の通り

- MT5をMAC上で実行し適切にMT5のMCPの設定を行う
- Docker上でcodexを実行
- codexは定期的に実行しチャートの分析を行い売買を行う

# 詳細

## 1. MCP経由でMT5からチャートの情報を取得

MCPから取得されるデータ情報

```
{
  "symbol": "BTCUSD",
  "period": "H1",
  "data_available_from": "2013.01.01 00:00:00",
  "history": [
    {
      "time": "2026.08.15 19:00:00",
      "open": 63075.25,
      "high": 63101.75,
      "low": 63040.25,
      "close": 63072.45,
      "tick_volume": 193,
      "spread": 4000
    }
  ]
}
```

## 2. こちらで用意したPythonを利用してテクニカルチャートの計算を行う

`scripts/technical_chart.py`は、MCPから取得したJSONを読み込み、次の結果を箇条書きで出力します。

- 短期移動平均線と長期移動平均線のクロス判定
- 短期移動平均線と中期移動平均線のクロス判定
- RSIの値

MCPから取得したJSON文字列を`--market-json`に指定して実行します。JSON文字列はシェルに展開されないようにシングルクォートで囲みます。

```bash
docker compose exec app python scripts/technical_chart.py \
  --ma-periods 5,20,60 \
  --ma-method SMA \
  --applied-price CLOSE \
  --rsi-period 14 \
  --bar-shift 1 \
  --market-json \
  '{"symbol":"BTCUSD","period":"H1","data_available_from":"2013.01.01 00:00:00","history":[{"time":"2026.08.15 19:00:00","open":63075.25,"high":63101.75,"low":63040.25,"close":63072.45,"tick_volume":193,"spread":4000}]}'
```

引数は次のとおりです。

- `--ma-periods`: 短期、中期、長期の移動平均期間。既定値は`5,20,60`
- `--ma-method`: 移動平均方式。`SMA`、`EMA`、`SMMA`、`LWMA`から指定。既定値は`SMA`
- `--applied-price`: MAとRSIの適用価格。`CLOSE`、`OPEN`、`HIGH`、`LOW`、`MEDIAN`、`TYPICAL`、`WEIGHTED`から指定。既定値は`CLOSE`
- `--rsi-period`: RSIの期間。既定値は`14`
- `--bar-shift`: 判定対象を最新バーから何本戻すか。`0`は最新バー、`1`は1本前。既定値は`1`
- `--market-json`: MCPから取得したJSON文字列

出力例

```text
- 短期移動平均線と長期移動平均線: ゴールデンクロス
- 短期移動平均線と中期移動平均線: クロスなし
- RSI: 63.42
```

指定した移動平均期間とRSI期間に対して履歴が不足している場合は、`判定不可（データ不足）`または`算出不可（データ不足）`を出力します。

## 3. 複数時間足の全履歴から移動平均クロスを抽出する

`scripts/find_ma_crosses.py`は、MCPから取得した複数時間足の履歴を一括で読み込み、各バーのSMA5、SMA20、SMA60を計算します。
SMA5とSMA20、SMA5とSMA60について、ゴールデンクロスとデッドクロスが成立したバーをJSONで出力します。

入力JSONは次の形式です。

```json
{
  "datasets": [
    {
      "symbol": "GOLD",
      "period": "M30",
      "history": [
        {
          "time": "2026.08.25 12:00:00",
          "open": 3400.0,
          "high": 3402.0,
          "low": 3398.0,
          "close": 3401.0,
          "tick_volume": 100,
          "spread": 20
        }
      ]
    }
  ]
}
```

`datasets`にはM5、M15、M30、H1など複数の時間足を指定できます。
2年間の開始直後からSMA60のクロスを判定する場合は、`history`に走査開始日時より前の確定足を60本以上含め、`--scan-from`で出力対象を2年間に絞ります。

```bash
docker compose exec -T app /project/.venv/bin/python scripts/find_ma_crosses.py \
  --input /project/market_history.json \
  --output /project/ma_crosses.json \
  --ma-periods 5,20,60 \
  --ma-method SMA \
  --applied-price CLOSE \
  --end-bar-shift 1 \
  --scan-from "2024.08.26 00:00:00" \
  --scan-to "2026.08.26 00:00:00"
```

- `--end-bar-shift 1`: 入力履歴の末尾1本を未確定足として走査対象から除外
- `--scan-from`: 出力対象の開始日時。この日時を含む
- `--scan-to`: 出力対象の終了日時。この日時を含まない
- `--input -`: ファイルの代わりに標準入力からJSONを読み込む
- `--output -`: ファイルの代わりに標準出力へJSONを出力する
- `--run-directory PATH`: 指定フォルダを作成し、入力を`market_history.json`、結果を`ma_crosses.json`として保存する
- `--run-directory`: パスを省略すると`data/タスク/2_Gold売買検証/1_Goldの売買タイミング/YYYYMMDDHHmm`を作成する

### 売却条件を一括検証する

`--backtest`を指定すると、M30のSMA5がSMA60をゴールデンクロスした買いシグナルに対し、次の62通りの売却条件を一括検証します。

買い時刻に、直前に確定したM5またはM15のどちらかでSMA5がSMA60を下回っている場合、その買いシグナルは使用しません。

- M5、M15、M30のSMA5対SMA20またはSMA60のデッドクロス: 6通り
- 0.25%、0.5%、0.75%、1%、1.5%、2%、3%、5%の利確: 8通り
- デッドクロスまたは利確の先着条件: 48通り

```bash
docker compose exec -T app /project/.venv/bin/python scripts/find_ma_crosses.py \
  --input /data/タスク/2_Gold売買検証/1_Goldの売買タイミング/202608271557/market_history.json \
  --run-directory /data/タスク/2_Gold売買検証/1_Goldの売買タイミング/202608271557 \
  --ma-periods 5,20,60 \
  --ma-method SMA \
  --applied-price CLOSE \
  --end-bar-shift 1 \
  --scan-from "2024.08.26 00:00:00" \
  --scan-to "2026.08.26 00:00:00" \
  --backtest \
  --profit-targets 0.25,0.5,0.75,1,1.5,2,3,5 \
  --point-size 0.01 \
  --digits 2 \
  --cost-mode none \
  --expected-symbol GOLD \
  --overwrite
```

実行フォルダには次のファイルを保存します。

- `market_history.json`: 入力したM5、M15、M30の市場データ
- `ma_crosses.json`: 各時間足で検出したクロス
- `backtest_trades.json`: 戦略別の全取引明細
- `backtest_summary.json`: 全期間の集計と順位

### M30とH1の買い条件を比較する

`--compare-entry-periods`を追加すると、次の14通りだけを検証します。

- M30のSMA5対SMA60ゴールデンクロスで買い、M5、M15、M30のSMA5対SMA20またはSMA60デッドクロスで売却: 6通り
- H1のSMA5対SMA60ゴールデンクロスで買い、M5、M15、M30、H1のSMA5対SMA20またはSMA60デッドクロスで売却: 8通り
- M30買いは、直前に確定したM5またはM15で`SMA5 < SMA60`の場合に見送る
- H1買いは、直前に確定したM5、M15、M30のいずれかで`SMA5 < SMA60`の場合に見送る

```bash
docker compose exec -T app /project/.venv/bin/python scripts/find_ma_crosses.py \
  --input /data/タスク/2_Gold売買検証/1_Goldの売買タイミング/202608271557/market_history.json \
  --run-directory /data/タスク/2_Gold売買検証/1_Goldの売買タイミング/202608271557 \
  --ma-periods 5,20,60 \
  --ma-method SMA \
  --applied-price CLOSE \
  --end-bar-shift 1 \
  --scan-from "2024.08.26 00:00:00" \
  --scan-to "2026.08.26 00:00:00" \
  --backtest \
  --compare-entry-periods \
  --cost-mode none \
  --overwrite
```

クロスは確定足で判定し、売買価格には次に存在する同時間足の始値を使います。
利確はM5の高値で到達を判定し、窓開け時はM5の始値、それ以外は目標価格で約定したものとして扱います。
同一戦略では同時に1ポジションだけ保有し、保有中の追加買いシグナルは無視します。
今回の検証ではスプレッド、手数料、スリッページ、スワップを含めません。
M5、M15、M30の共通範囲だけを検証し、入力データを識別するSHA-256を各結果ファイルへ記録します。
既存ファイルは`--overwrite`を指定した場合だけ置き換えます。

# 実行プロンプト

`PROMPT.md`を参照する

# サンプル

```bash
docker compose exec app python scripts/technical_chart.py \
  --ma-periods 5,20,60 \
  --ma-method SMA \
  --applied-price CLOSE \
  --rsi-period 14 \
  --bar-shift 1 \
  --market-json \
  '{"symbol": "BTCUSD","period": "H1","data_available_from": "2013.01.01 00:00:00","history": [{"time": "2026.08.13 12:00:00","open": 63752.45,"high": 63766.25,"low": 63561.55,"close": 63758.35,"tick_volume": 564,"spread": 4000},{"time": "2026.08.13 13:00:00","open": 63759.65,"high": 63798.05,"low": 63557.85,"close": 63630.15,"tick_volume": 491,"spread": 4000},{"time": "2026.08.13 14:00:00","open": 63630.15,"high": 63642.05,"low": 63328.25,"close": 63465.45,"tick_volume": 696,"spread": 4000},{"time": "2026.08.13 15:00:00","open": 63465.45,"high": 63692.45,"low": 63423.55,"close": 63677.25,"tick_volume": 1003,"spread": 4000},{"time": "2026.08.13 16:00:00","open": 63677.8,"high": 63833.65,"low": 63541.35,"close": 63706.55,"tick_volume": 2026,"spread": 4000},{"time": "2026.08.13 17:00:00","open": 63707.85,"high": 63987.95,"low": 63664.85,"close": 63845.25,"tick_volume": 1894,"spread": 4000},{"time": "2026.08.13 18:00:00","open": 63847.05,"high": 63927.35,"low": 63394.55,"close": 63394.55,"tick_volume": 1730,"spread": 4000},{"time": "2026.08.13 19:00:00","open": 63396.35,"high": 63550.55,"low": 62829.35,"close": 63140.75,"tick_volume": 2569,"spread": 4000},{"time": "2026.08.13 20:00:00","open": 63141.25,"high": 63277.85,"low": 63054.35,"close": 63126.05,"tick_volume": 1589,"spread": 4000},{"time": "2026.08.13 21:00:00","open": 63125.4,"high": 63346.85,"low": 63094.95,"close": 63286.45,"tick_volume": 923,"spread": 4000},{"time": "2026.08.13 22:00:00","open": 63286.45,"high": 63494.15,"low": 63286.45,"close": 63389.85,"tick_volume": 1102,"spread": 4000},{"time": "2026.08.13 23:00:00","open": 63388.35,"high": 63452.35,"low": 63344.95,"close": 63388.95,"tick_volume": 532,"spread": 4000},{"time": "2026.08.14 00:00:00","open": 63388.75,"high": 63485.25,"low": 63369.55,"close": 63451.05,"tick_volume": 493,"spread": 4000},{"time": "2026.08.14 01:00:00","open": 63451.95,"high": 63620.95,"low": 63451.95,"close": 63506.15,"tick_volume": 683,"spread": 4000},{"time": "2026.08.14 02:00:00","open": 63506.15,"high": 63517.05,"low": 63376.95,"close": 63466.05,"tick_volume": 506,"spread": 4000},{"time": "2026.08.14 03:00:00","open": 63467.25,"high": 63539.75,"low": 63388.95,"close": 63521.85,"tick_volume": 937,"spread": 4000},{"time": "2026.08.14 04:00:00","open": 63521.55,"high": 63569.65,"low": 63437.95,"close": 63551.15,"tick_volume": 970,"spread": 4000},{"time": "2026.08.14 05:00:00","open": 63552.75,"high": 63599.65,"low": 63447.15,"close": 63447.15,"tick_volume": 700,"spread": 4000},{"time": "2026.08.14 06:00:00","open": 63447.15,"high": 63558.15,"low": 63282.75,"close": 63292.45,"tick_volume": 622,"spread": 4000},{"time": "2026.08.14 07:00:00","open": 63292.45,"high": 63452.65,"low": 63252.85,"close": 63354.25,"tick_volume": 475,"spread": 4000},{"time": "2026.08.14 08:00:00","open": 63354.25,"high": 63380.15,"low": 63264.45,"close": 63362.75,"tick_volume": 487,"spread": 4000},{"time": "2026.08.14 09:00:00","open": 63362.75,"high": 63362.75,"low": 62917.05,"close": 62951.15,"tick_volume": 794,"spread": 4000},{"time": "2026.08.14 10:00:00","open": 62951.15,"high": 63085.85,"low": 62894.65,"close": 62896.55,"tick_volume": 656,"spread": 4000},{"time": "2026.08.14 11:00:00","open": 62896.55,"high": 62969.05,"low": 62667.95,"close": 62914.55,"tick_volume": 1149,"spread": 4000},{"time": "2026.08.14 12:00:00","open": 62914.55,"high": 62914.55,"low": 62715.05,"close": 62768.95,"tick_volume": 647,"spread": 4000},{"time": "2026.08.14 13:00:00","open": 62766.95,"high": 62919.85,"low": 62715.15,"close": 62794.85,"tick_volume": 618,"spread": 4000},{"time": "2026.08.14 14:00:00","open": 62794.85,"high": 62909.85,"low": 62784.85,"close": 62843.35,"tick_volume": 680,"spread": 4000},{"time": "2026.08.14 15:00:00","open": 62844.65,"high": 62889.35,"low": 62698.45,"close": 62717.15,"tick_volume": 912,"spread": 4000},{"time": "2026.08.14 16:00:00","open": 62717.35,"high": 62802.25,"low": 62575.15,"close": 62644.65,"tick_volume": 2392,"spread": 4000},{"time": "2026.08.14 17:00:00","open": 62646.35,"high": 62775.85,"low": 62514.95,"close": 62593.95,"tick_volume": 2081,"spread": 4000},{"time": "2026.08.14 18:00:00","open": 62591.05,"high": 63067.15,"low": 62588.35,"close": 62967.05,"tick_volume": 1483,"spread": 4000},{"time": "2026.08.14 19:00:00","open": 62965.45,"high": 63198.95,"low": 62945.55,"close": 63116.15,"tick_volume": 1092,"spread": 4000},{"time": "2026.08.14 20:00:00","open": 63118.25,"high": 63227.55,"low": 62977.55,"close": 62994.85,"tick_volume": 898,"spread": 4000},{"time": "2026.08.14 21:00:00","open": 62994.95,"high": 63060.25,"low": 62868.75,"close": 62908.15,"tick_volume": 796,"spread": 4000},{"time": "2026.08.14 22:00:00","open": 62909.75,"high": 62977.65,"low": 62804.65,"close": 62948.6,"tick_volume": 779,"spread": 4000},{"time": "2026.08.14 23:00:00","open": 62947.95,"high": 63003.35,"low": 62875.95,"close": 62886.25,"tick_volume": 477,"spread": 4000},{"time": "2026.08.15 00:00:00","open": 62886.25,"high": 62916.65,"low": 62851.75,"close": 62907.45,"tick_volume": 411,"spread": 4000},{"time": "2026.08.15 01:00:00","open": 62907.45,"high": 62941.25,"low": 62774.95,"close": 62862.55,"tick_volume": 436,"spread": 4000},{"time": "2026.08.15 02:00:00","open": 62862.55,"high": 63043.85,"low": 62842.45,"close": 63019.25,"tick_volume": 463,"spread": 4000},{"time": "2026.08.15 03:00:00","open": 63019.25,"high": 63039.65,"low": 62973.65,"close": 62992.45,"tick_volume": 318,"spread": 4000},{"time": "2026.08.15 04:00:00","open": 62994.25,"high": 63019.55,"low": 62970.45,"close": 63013.55,"tick_volume": 250,"spread": 4000},{"time": "2026.08.15 05:00:00","open": 63013.55,"high": 63115.25,"low": 63011.15,"close": 63114.95,"tick_volume": 293,"spread": 4000},{"time": "2026.08.15 06:00:00","open": 63114.95,"high": 63165.95,"low": 63021.35,"close": 63030.05,"tick_volume": 282,"spread": 4000},{"time": "2026.08.15 07:00:00","open": 63031.25,"high": 63129.25,"low": 63031.25,"close": 63098.85,"tick_volume": 175,"spread": 4000},{"time": "2026.08.15 08:00:00","open": 63098.85,"high": 63133.85,"low": 63018.25,"close": 63061.55,"tick_volume": 184,"spread": 4000},{"time": "2026.08.15 09:00:00","open": 63061.55,"high": 63111.65,"low": 63012.05,"close": 63092.25,"tick_volume": 271,"spread": 4000},{"time": "2026.08.15 10:00:00","open": 63092.25,"high": 63094.35,"low": 63021.05,"close": 63055.75,"tick_volume": 101,"spread": 4000},{"time": "2026.08.15 11:00:00","open": 63056.2,"high": 63069.45,"low": 62904.05,"close": 62911.55,"tick_volume": 240,"spread": 4000},{"time": "2026.08.15 12:00:00","open": 62911.55,"high": 63022.25,"low": 62895.95,"close": 63007.65,"tick_volume": 234,"spread": 4000},{"time": "2026.08.15 13:00:00","open": 63007.65,"high": 63028.65,"low": 62979.05,"close": 63019.35,"tick_volume": 171,"spread": 4000},{"time": "2026.08.15 14:00:00","open": 63018.95,"high": 63037.05,"low": 62965.95,"close": 62993.15,"tick_volume": 213,"spread": 4000},{"time": "2026.08.15 15:00:00","open": 62993.15,"high": 63033.95,"low": 62924.15,"close": 63031.35,"tick_volume": 194,"spread": 4000},{"time": "2026.08.15 16:00:00","open": 63028.75,"high": 63060.45,"low": 62998.85,"close": 63034.25,"tick_volume": 292,"spread": 4000},{"time": "2026.08.15 17:00:00","open": 63034.25,"high": 63034.25,"low": 62970.45,"close": 63016.75,"tick_volume": 185,"spread": 4000},{"time": "2026.08.15 18:00:00","open": 63016.75,"high": 63099.55,"low": 63003.45,"close": 63075.25,"tick_volume": 186,"spread": 4000},{"time": "2026.08.15 19:00:00","open": 63075.25,"high": 63101.75,"low": 63040.25,"close": 63072.45,"tick_volume": 193,"spread": 4000},{"time": "2026.08.15 20:00:00","open": 63072.45,"high": 63079.75,"low": 63038.65,"close": 63048.05,"tick_volume": 109,"spread": 4000},{"time": "2026.08.15 21:00:00","open": 63048.05,"high": 63088.15,"low": 63000.05,"close": 63029.95,"tick_volume": 164,"spread": 4000},{"time": "2026.08.15 22:00:00","open": 63029.95,"high": 63099.95,"low": 63029.95,"close": 63099.95,"tick_volume": 154,"spread": 4000},{"time": "2026.08.15 23:00:00","open": 63099.95,"high": 63099.95,"low": 63049.85,"close": 63079.95,"tick_volume": 122,"spread": 4000},{"time": "2026.08.16 00:00:00","open": 63077.65,"high": 63142.05,"low": 63077.55,"close": 63131.85,"tick_volume": 138,"spread": 4000},{"time": "2026.08.16 01:00:00","open": 63131.85,"high": 63150.45,"low": 63104.65,"close": 63109.15,"tick_volume": 138,"spread": 4000},{"time": "2026.08.16 02:00:00","open": 63109.15,"high": 63118.25,"low": 63061.35,"close": 63065.05,"tick_volume": 136,"spread": 4000},{"time": "2026.08.16 03:00:00","open": 63065.05,"high": 63065.15,"low": 63043.95,"close": 63059.95,"tick_volume": 124,"spread": 4000},{"time": "2026.08.16 04:00:00","open": 63058.65,"high": 63060.55,"low": 62982.95,"close": 63058.05,"tick_volume": 220,"spread": 4000},{"time": "2026.08.16 05:00:00","open": 63057.05,"high": 63132.05,"low": 63000.25,"close": 63100.05,"tick_volume": 266,"spread": 4000},{"time": "2026.08.16 06:00:00","open": 63100.05,"high": 63105.05,"low": 63088.05,"close": 63103.75,"tick_volume": 87,"spread": 4000},{"time": "2026.08.16 07:00:00","open": 63103.75,"high": 63128.35,"low": 63065.35,"close": 63065.35,"tick_volume": 134,"spread": 4000},{"time": "2026.08.16 08:00:00","open": 63065.35,"high": 63075.75,"low": 63015.15,"close": 63038.35,"tick_volume": 153,"spread": 4000},{"time": "2026.08.16 09:00:00","open": 63038.35,"high": 63062.65,"low": 63018.05,"close": 63046.95,"tick_volume": 146,"spread": 4000}]}'
```
