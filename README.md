# TASO-SWINDLE

TASO-SWINDLE は、やねうら王系エンジンをバックエンドに使う独立型 USI ラッパーです。  
通常の「評価値最大化」ではなく、不利局面での対人逆転期待値を重視して候補手を選び直します。

## 何をするソフトか

- USI 互換のまま動くラッパーです
- バックエンドエンジンの `info` を解析し、逆転狙い向けに手を再順位付けします
- `score mate`、OnlyMovePressure、ReplyEntropy、Trap 系特徴、Risk 系特徴を使って候補を評価します
- 通常の最善手エンジンとは別思想で、「人間相手に逆転しやすい手」を狙います

## 向いている使い方

- 不利局面での逆転狙い
- 観戦・検討で「いやらしい受けづらさ」を見たいとき
- 通常の強いエンジンとは別に、変化球のエンジンを使いたいとき

## 必要なもの

- Python 3.11 以上
- やねうら王系バックエンド実行ファイル
- バックエンド用の評価関数ディレクトリ

このリポジトリには `eval/nn.bin` は含めていません。  
実行時には、自分で用意した評価関数一式を `eval/` 配下に置いてください。

最小構成の例:

```text
TASO-SWINDLE/
  launch_taso_swindle.command
  YaneuraOu
  eval/
    nn.bin
  models/
    hybrid_weights.json
  taso_swindle/
```

## 最短導入

### 配布版を使う場合

1. Release の part ファイルをすべて取得します
2. `RESTORE_FROM_PARTS_JA.txt` の手順で zip を復元・展開します
3. 展開先に `YaneuraOu` と `eval/nn.bin` が揃っていることを確認します
4. GUI に `launch_taso_swindle.command` を登録します

### リポジトリ版を使う場合

1. このリポジトリを配置します
2. 同じディレクトリに `YaneuraOu` を置きます
3. `eval/nn.bin` を含む `eval/` を用意します
4. GUI には `launch_taso_swindle.command` を登録するか、`python3 -m taso_swindle.main` を使います

## GUI 別の導入例

### ShogiHome

1. エンジン管理を開きます
2. 新規追加で [launch_taso_swindle.command](./launch_taso_swindle.command) を選びます
3. エンジンオプションで次を確認します
   - `BackendEnginePath`
   - `BackendEngineOptionPassthrough`
   - `SwindleEnable`
   - `SwindleMode`
   - `SwindleHybridWeightsPath`
4. 保存後、`isready` が通れば使用できます

ShogiHome での最小例:

```text
BackendEnginePath=/absolute/path/to/YaneuraOu
BackendEngineOptionPassthrough=Threads=8;Hash=8192;EvalDir=/absolute/path/to/eval;BookFile=no_book
SwindleEnable=true
SwindleMode=HYBRID
SwindleUseHybridLearnedAdjustment=true
SwindleHybridWeightsPath=/absolute/path/to/models/hybrid_weights.json
```

### 将棋所

1. エンジン登録で [launch_taso_swindle.command](./launch_taso_swindle.command) を指定します
2. オプション設定からバックエンドパスと評価関数パスを通します
3. 初回は `BackendEnginePath` と `BackendEngineOptionPassthrough` を優先して設定します

将棋所では次の組み合わせが扱いやすいです:

```text
BackendEnginePath=/absolute/path/to/YaneuraOu
BackendEngineOptionPassthrough=Threads=8;Hash=8192;EvalDir=/absolute/path/to/eval;BookFile=no_book
SwindleEnable=true
SwindleMode=HYBRID
SwindleVerifyMode=VERIFY_ONLY
```

### 将棋GUI

1. エンジン設定から [launch_taso_swindle.command](./launch_taso_swindle.command) を登録します
2. エンジンオプションでバックエンドと評価関数の場所を指定します
3. 逆転モードを使うなら `SwindleEnable=true` のまま利用します

将棋GUI 向けの推奨初期値:

```text
BackendEnginePath=/absolute/path/to/YaneuraOu
BackendEngineOptionPassthrough=Threads=8;Hash=8192;EvalDir=/absolute/path/to/eval;BookFile=no_book
SwindleEnable=true
SwindleMode=HYBRID
SwindleVerifyMode=VERIFY_ONLY
SwindleUseHybridLearnedAdjustment=true
SwindleHybridWeightsPath=/absolute/path/to/models/hybrid_weights.json
SwindleUsePonderGateLearnedAdjustment=false
SwindleLogEnable=false
SwindleVerboseInfo=false
```

## 推奨設定

普段使いなら次で十分です。

- `SwindleEnable=true`
- `SwindleMode=HYBRID`
- `SwindleVerifyMode=VERIFY_ONLY`
- `SwindleUseHybridLearnedAdjustment=true`
- `SwindleUsePonderGateLearnedAdjustment=false`

バックエンド側の目安:

- `Threads=4` から `8`
- `Hash=2048` から `8192`

32GB クラスの Mac なら、まず `Threads=8` と `Hash=8192` で始めるのが無難です。

## 主なモード

### `HYBRID`

標準のおすすめです。  
攻め筋と罠筋のバランスがよく、いちばん使いやすいです。

### `TACTICAL`

詰み・王手・露骨な圧力を重視します。  
終盤の殴り合い向けです。

### `MURKY`

難解化、応手の読みづらさ、罠っぽさを重視します。  
中終盤で人間のミス待ちを強めたいとき向けです。

### `AUTO`

局面に応じて自動で切り替えます。  
まずは `HYBRID` で十分ですが、試す価値はあります。

## 参考棋力（将棋クラブ24換算の目安）

以下は公式レートではなく、ローカルでの ELO 校正結果と運用時の挙動から置いた参考値です。  
基準は「同条件のやねうら王バックエンドを将棋クラブ24 4300〜4500 相当とみなす」近似で、そこからの相対差で換算しています。

| 設定 | 将棋クラブ24換算の目安 | 根拠 |
| --- | --- | --- |
| `SwindleEnable=false` | 約 `4240〜4440` | 実測あり。baseline 比 `-59.6 Elo` |
| `SwindleDryRun=true` | 約 `4240〜4440` | 実戦上は backend `bestmove` を返すため、`OFF` に近い扱い |
| `SwindleMode=HYBRID` | 約 `3570〜3770` | 実測あり。baseline 比 `-726.9 Elo` |
| `SwindleMode=AUTO` | 約 `3550〜3800` | 未個別校正。`HYBRID` 近傍の運用目安 |
| `SwindleMode=TACTICAL` | 約 `3480〜3730` | 未個別校正。攻撃寄りで振れ幅が出やすい前提の目安 |
| `SwindleMode=MURKY` | 約 `3420〜3680` | 未個別校正。難解化寄りで安定度が下がる前提の目安 |

補足:

- `HYBRID` と `OFF` はローカル校正結果に基づく値です
- `AUTO/TACTICAL/MURKY` は現時点で固定条件の個別校正を取っていないため、実測ではなく暫定帯です
- 「勝ちやすさ」ではなく「逆転の味」を重視する設計なので、逆転モード ON 時は通常エンジンより安定レートが下がるのが前提です

## DryRun と本選択

### `SwindleDryRun=true`

- 内部では逆転評価を計算します
- 返す手はバックエンドの通常 `bestmove` です
- 比較検証やログ確認向けです

### `SwindleDryRun=false`

- 逆転評価の上位手を返します
- 実戦で使うならこちらです

## よく使うオプション

- `BackendEnginePath`
- `BackendEngineArgs`
- `BackendEngineOptionPassthrough`
- `SwindleEnable`
- `SwindleMode`
- `SwindleVerifyMode`
- `SwindleHybridWeightsPath`
- `SwindleUseHybridLearnedAdjustment`
- `SwindleLogEnable`
- `SwindleVerboseInfo`

## コマンドライン起動

直接起動する場合:

```bash
python3 -m taso_swindle.main
```

最低限の USI 設定例:

```text
setoption name BackendEnginePath value /absolute/path/to/YaneuraOu
setoption name BackendEngineArgs value -eval /absolute/path/to/eval
setoption name SwindleEnable value true
setoption name SwindleMode value HYBRID
isready
```

## 配布版について

Discord 向けの分割 bundle を作る仕組みがあります。  
配布版には `hybrid_weights.json` を含められますが、学習ログや棋譜は含めません。

関連スクリプト:

- `scripts/build_discord_release.py`
- `scripts/restore_discord_parts.py`
- `scripts/scan_release_privacy.py`

## 注意

- 本リポジトリ本体には `eval/nn.bin` は含みません
- GUI で使うときは、まずバックエンド単体が正常に動くことを確認してください
- 逆転特化ラッパーなので、常に通常エンジンより安定して強くなることを目的にしたソフトではありません
- 学習・検証・運用用スクリプトは `scripts/` に残していますが、通常利用では読む必要はありません

## ライセンス

ソースコードの扱いは [LICENSE](./LICENSE) を参照してください。  
第三者エンジン、評価関数、バイナリ、関連アセットはこのリポジトリのライセンス対象外です。
