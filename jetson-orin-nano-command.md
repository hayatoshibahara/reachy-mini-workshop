python -m speech_to_speech.s2s_pipeline --stt faster-whisper --stt_model_name distil-large-v3 --faster_whisper_stt_device cpu --faster_whisper_stt_compute_type int8 --language ja --llm_backend responses-api --model_name qwen3.5:0.8B --responses_api_base_url http://127.0.0.1:11434/v1 --responses_api_api_key dummy --tts kokoro --kokoro_lang_code j --kokoro_device cpu --kokoro_voice jf_alpha --mode realtime --enable_live_transcription

reachy-mini-daemon

reachy-mini-conversation-app --debug
