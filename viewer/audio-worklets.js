// マイク・スピーカー用の AudioWorklet。
// AudioContext のサンプリングレート（多くは 48kHz）とサーバー側のレートの差は、
// ブラウザごとの違いを避けるため、ここで線形補間して変換する。

// マイク: 入力を 16kHz・16bit・モノラルにして、20ms ごとにメインスレッドへ送る
class MicCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.outRate = options.processorOptions.rate;
    this.step = sampleRate / this.outRate;
    this.pos = 0; // 次に取り出す位置（入力サンプル単位、prev を -1 とする）
    this.prev = 0;
    this.chunk = new Int16Array(this.outRate / 50);
    this.filled = 0;
    this.peak = 0;
  }

  process(inputs) {
    const input = inputs[0][0];
    if (!input) return true;
    for (const s of input) this.peak = Math.max(this.peak, Math.abs(s));

    while (this.pos < input.length) {
      const i = Math.floor(this.pos);
      const frac = this.pos - i;
      const a = i === 0 ? this.prev : input[i - 1];
      const b = input[i];
      // pos は「prev の次」を 0 とした位置なので、[i-1, i] の間を補間する
      const v = a + (b - a) * frac;
      this.chunk[this.filled++] = Math.max(-1, Math.min(1, v)) * 0x7fff;
      if (this.filled === this.chunk.length) {
        this.port.postMessage({ pcm: this.chunk.buffer, peak: this.peak }, [this.chunk.buffer]);
        this.chunk = new Int16Array(this.outRate / 50);
        this.filled = 0;
        this.peak = 0;
      }
      this.pos += this.step;
    }
    this.pos -= input.length;
    this.prev = input[input.length - 1];
    return true;
  }
}

// スピーカー: サーバーから届く 24kHz・16bit・モノラルを、少しためてから再生する
class SpeakerPlayer extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const inRate = options.processorOptions.rate;
    this.step = inRate / sampleRate;
    this.prebuffer = inRate * 0.08; // 80ms たまったら再生を始める
    this.maxBuffer = inRate * 0.4; // 400ms を超えたら古い分を捨てて遅れを戻す
    this.queue = []; // Float32Array の列
    this.queued = 0;
    this.offset = 0; // queue[0] の中の読み出し位置（小数）
    this.playing = false;
    this.port.onmessage = (e) => {
      const pcm = new Int16Array(e.data);
      const f = new Float32Array(pcm.length);
      for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 0x8000;
      this.queue.push(f);
      this.queued += f.length;
      while (this.queued - this.offset > this.maxBuffer && this.queue.length > 1) {
        this.queued -= this.queue.shift().length;
        this.offset = 0;
      }
    };
  }

  sampleAt(k) {
    // queue の先頭から k 番目のサンプル（k は整数）
    for (const f of this.queue) {
      if (k < f.length) return f[k];
      k -= f.length;
    }
    return 0;
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!this.playing && this.queued - this.offset >= this.prebuffer) this.playing = true;
    if (!this.playing) {
      out.fill(0);
      return true;
    }
    for (let n = 0; n < out.length; n++) {
      if (this.queued - this.offset < 2) {
        // 足りなくなったら無音にして、またためる
        out.fill(0, n);
        this.playing = false;
        break;
      }
      const i = Math.floor(this.offset);
      const frac = this.offset - i;
      const a = this.sampleAt(i);
      const b = this.sampleAt(i + 1);
      out[n] = a + (b - a) * frac;
      this.offset += this.step;
      while (this.queue.length && this.offset >= this.queue[0].length) {
        this.offset -= this.queue[0].length;
        this.queued -= this.queue.shift().length;
      }
    }
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);
    return true;
  }
}

registerProcessor("mic-capture", MicCapture);
registerProcessor("speaker-player", SpeakerPlayer);
