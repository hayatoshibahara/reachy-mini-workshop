# LLM 拡張プラン — 応答にマッチしたモーション/ダンスの再生

## ゴール

LLMの応答テキストと同時に、内容に合ったモーションを選ばせて再生する。
使えるモーションは emotions library 約65種 + dances library 19種 = **約84種**。

## 「GPTに出力させる」方針への意見

**賛成。conversation_app も同じ設計**(LLMに `play_emotion` ツールを与え、
応答に合わせて感情モーションを選ばせている)なので、実績のあるアプローチ。
ただし2点だけ工夫したい:

1. **自由テキストではなく Structured Outputs で出させる**。
   「応答の末尾に [motion: happy1] を付けて」方式はパースが壊れる・
   モーション名を幻覚する事故が起きる。JSON Schema の enum で縛れば
   存在しない名前は構造的に出せなくなる
2. **生のモーション名84種を直接選ばせない方が良い**。理由は下記

## 選択方式の比較

### A案: 全84種のモーション名を enum で直接選ばせる

- 長所: 実装が単純。ダンスも含め全モーションが使える
- 短所: `amazed1` `boredom2` のような名前だけでは動きが想像できず、
  LLMの選択品質が名前の雰囲気頼みになる。**モーションの品質にはバラつきがあり**、
  conversation_app はわざわざ「優良リスト」(`_EXCELLENT_MOVES` 20種 +
  `_OK_CLEAR_MOVES` 27種)を定義して低品質なものを除外している

### B案(推奨): 意図(intent)を選ばせ、コードでモーション名に変換

conversation_app の `play_emotion.py` の設計をそのまま流用する:

- LLM は `happy` / `thinking` / `surprised` / `dance` など**約40種の意図**から選ぶ
  (`EMOTION_INTENTS` のリストをほぼ流用)
- コード側で `_INTENT_TO_MOVES` の対応表(例: `"happy": ("laughing2", "laughing1")`)
  からランダムに1つ選んで再生 → 同じ意図でも動きに変化が出る
- `dance` 系の意図には dances library の19種を割り当てて拡張
  (`"dance": ("groovy_sway_and_roll", "side_to_side_sway", ...)`)
- 対応表は人間がキュレーション済みなので、変な動きが混ざらない

**推奨理由**: LLMは「どんな感情か」の判断が得意で、「このモーション名が
どう動くか」は知らない。得意な部分だけ任せるのがB案。

## 出力スキーマ(Structured Outputs)

```python
schema = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "博多弁の応答(1〜2文)"},
        "intent": {"type": "string", "enum": EMOTION_INTENTS},  # 約40種
    },
    "required": ["reply", "intent"],
    "additionalProperties": False,
}

resp = client.responses.create(
    model="gpt-5.4-mini",
    instructions=SYSTEM_PROMPT,  # 博多弁ルール + intent選択の指示を追記
    input=history + [{"role": "user", "content": text}],
    text={"format": {"type": "json_schema", "name": "reply_with_motion",
                     "schema": schema, "strict": True}},
    reasoning={"effort": "minimal"},
    max_output_tokens=200,
)
```

システムプロンプトへの追記:

```
応答と同時に、内容に合う感情・動きを intent から1つ選ぶ。
楽しい話や音楽の話なら dance 系、質問されて考えるときは thinking、
褒められたら happy や grateful、のように応答の気分と一致させる。
```

## 再生の統合(main.py)

```
[LLMスレッド] (reply, intent) → intent を move 名に解決 → response_queue に put
[モーションループ] response_queue に move があれば最優先で再生
                   (再生中の idle/talking モーションは cancel して差し替え)
                   再生後は通常の VAD 駆動に戻る
```

- 起動時に emotions + dances の両ライブラリをロードし、対応表の全 move 名を
  `list_moves()` で検証(存在しない名前は起動時に検出)
- intent 解決に失敗した場合のフォールバックは `attentive1`(無難な相槌)
- 応答モーションは **sound=True で再生する選択肢もある**(emotions には
  効果音が付属しており、返答の感情表現として効く)。うるさければ False に
- ダンスは長い(10〜20秒)ので、再生中に次の発話が来たら cancel で中断される
  (既存の仕組みがそのまま効く)

## コストへの影響

intent の enum(約40語)はスキーマとしてプロンプトに入るが数百トークン程度。
システムプロンプトと合わせて prompt caching が効くので、1往復あたりの
増分は実質ゼロに近い(llm.md の試算 0.01円/往復 のまま)。

## 実装ステップ

1. `play_emotion.py` から `EMOTION_INTENTS` と `_INTENT_TO_MOVES` を移植
   (import はせずコピー: conversation_app への依存を作らない)
2. dance 系 intent に dances library の move 名を追加
3. LLM呼び出しを Structured Outputs 化、`(reply, intent)` を受け取る
4. `response_queue` を追加し、motion_loop の優先再生を実装
5. 起動時バリデーション + フォールバック

## 検証手順

1. 「何か踊って」→ dance 系 intent が選ばれダンスが再生されること
2. 「すごいね!」→ happy/grateful 系のモーションが出ること
3. 対応表の全 move 名が両ライブラリに実在すること(起動時チェック)
4. ダンス再生中に話しかけたら中断されて talking モーションに切替わること
5. intent とテキストの感情がズレるケースがないか10往復ほど会話して確認
