# Local models, dictionary data and TTS tooling: where everything is, how to run it, how to remove it

Written 2026-10-13 for the `main-asw1kr` work. Everything below was installed on **this Windows PC** (user `Sujith`).
Nothing here is part of the experiment evidence: synthetic speech and dictionary suggestions are **software-validation aids only**
(see `CLAUDE.md` gates). Large files live in `data/private/` (git-ignored) or in user-level caches, never in git.

## 1. Map of everything (path, size, purpose, how to remove)

### 1a. Installed for the Sound2Word work (safe to delete, re-creatable)

| What | Path | Size | Purpose | Remove |
|---|---|---|---|---|
| Open dictionaries (downloads) | `D:\Apps\Sound2Word\data\private\dictionary\raw\` (`datuk`, `enml`, `ekkurup` + `.tar.gz`) | 0.08 GB | Olam Datuk (ODbL), Olam enml (ODbL), E.K. Kurup (CC BY-SA 4.0) | delete the folder |
| Combined lexicon | `D:\Apps\Sound2Word\data\private\dictionary\lexicon.csv` | 0.12 GB | 257,684 Malayalam words, sounds, meanings, sources | delete the file (rebuild: section 2.2) |
| mlphon env | `D:\Apps\Sound2Word\data\private\venv_mlphon\` | ~0.05 GB | Python env with `mlphon` (Malayalam pronunciation) for the lexicon builder | delete the folder |
| TTS tooling | `D:\Apps\Sound2Word\data\private\tts\VyvoTTS\` (git clone, MIT), `...\tts\pkgs\` (`snac`, `einops`) | <0.01 GB | runs the LFM Malayalam TTS inside Rootit's `.venv-indic` | delete both folders |
| Parler env | `D:\Apps\Sound2Word\data\private\tts\venv_parler\` | 5.0 GB | Python env: torch 2.4.1+cu121, parler-tts, transformers 4.46.1 | delete the folder |
| Generated clips | `D:\Apps\Sound2Word\data\private\tts\lfm_first10\`, `lfm_common10\`, `parler\<set>_<voice>\` | ~0.02 GB | SYNTHETIC reference audio + `meta.json` / `meta_scored.json` | delete the folders |
| Voice recordings | `D:\Apps\Sound2Word\data\private\voice_demo\` | small | your accepted takes, `words.json`, `candidates_unverified.csv` | **keep** unless you want to lose your list/takes |
| LFM Malayalam TTS model | `C:\Users\Sujith\.cache\huggingface\hub\models--Praha-Labs--LFM-MALAYALAM-TTS-v0.1\` | 0.73 GB | TTS candidate (Apache-2.0) | delete the folder |
| SNAC 24 kHz codec | `...\hub\models--hubertsiuzdak--snac_24khz\` | 0.07 GB | audio codec used by LFM | delete the folder |
| Indic Parler-TTS model | `...\hub\models--ai4bharat--indic-parler-tts\` | 3.5 GB | TTS candidate (Apache-2.0, gated) | delete the folder |
| Flan-T5 tokenizer | `...\hub\models--google--flan-t5-large\` | ~0 GB | description tokenizer for Parler | delete the folder |
| Hugging Face login | `C:\Users\Sujith\.cache\huggingface\token` (also saved as a git credential) | tiny | lets Parler download | see section 5 |
| Docker images | `ubuntu:24.04` (119 MB), `mcr.microsoft.com/playwright:v1.49.0-noble` (3.5 GB) | 3.6 GB | recorder container; browser-test runner | `docker rmi <image>` |
| Docker container | `s2w-rec` | n/a | the running recorder | `docker rm -f s2w-rec` |
| Eval scripts | `D:\Apps\Sound2Word\tools\model_eval\` (in git) | tiny | benchmark / generate / score | part of the repo |

### 1b. Pre-existing, NOT installed by this work (do not delete)

These belong to **Rootit Voice** (`D:\Apps\Rootit Voice`). The Sound2Word tooling only *reads* them.

| What | Path | Notes |
|---|---|---|
| `indic-core` (Bodhan AI, 4.9 GB) | `%APPDATA%\rootit-voice\models\indic-core\` | best Malayalam recogniser measured; text only, no timestamps |
| `vegam-ml` (Whisper medium Malayalam) | `%APPDATA%\rootit-voice\models\vegam-ml\` | word timestamps, weaker recognition |
| `large-v3` | `%APPDATA%\rootit-voice\models\large-v3\` | **incomplete** (`model.bin.part`); the HF-cache copy is also incomplete |
| `large-v3-turbo` | `...\hub\models--mobiuslabsgmbh--faster-whisper-large-v3-turbo\` | works, poor on sung Malayalam |
| Rootit Python envs | `D:\Apps\Rootit Voice\backend\.venv-py310\` (torch 2.0.1 CPU, faster-whisper), `...\.venv-indic\` (torch 2.4.1+cu121, transformers 4.57.6) | reused to run models; **not modified** (extra packages go in `data\private\tts\pkgs`) |
| Rootit API | port **8780** | do not reuse this port |

## 2. How to run things

All commands are PowerShell. Repo root: `D:\Apps\Sound2Word`. Docker Desktop must be in Linux-containers mode.

### 2.1 Recorder (Docker), with dictionary and AI reference voices

```powershell
docker rm -f s2w-rec 2>$null
$T='D:\Apps\Sound2Word\data\private\tts'
docker run -d --name s2w-rec -p 127.0.0.1:8765:8766 `
  -v "D:\Apps\Sound2Word:/src:ro" `
  -v "D:\Apps\Sound2Word\data\private\voice_demo:/voice" `
  -v "D:\Apps\Sound2Word\data\private\dictionary:/dict:ro" `
  -v "$T\lfm_first10:/ref/lfm_first10:ro" -v "$T\lfm_common10:/ref/lfm_common10:ro" -v "$T\parler_cap_dot_trim200:/ref/parler_cap_dot_trim200:ro" `
  -v "$T\units_anjali_trim:/units:ro" `
  -v "$T\top50_lfm_trim\units_raw:/units_lfm:ro" -v "$T\top50_harish_trim\units_raw:/units_harish:ro" `
  ubuntu:24.04 bash /src/tools/run_recorder_container.sh
```

The startup logic (apt, build, mlphon, socat, recorder flags) is in `tools/run_recorder_container.sh`; edit the last line there to change flags.
(Inline `bash -c` with quotes does not survive Windows PowerShell 5.1, which strips embedded double quotes: use the script file.)

Startup takes 1-3 minutes (apt + build + mlphon). Then open **http://127.0.0.1:8765** (use exactly this address: the microphone needs it).
Deep link to a word: `http://127.0.0.1:8765/?word=kalam&q=kal&tab=mine`. Stop: `docker rm -f s2w-rec`.

The **AI reference voices** panel appears under the selected word: a play button per synthetic clip, the model and voice, and what
`indic-core` heard (✓ matches / ✗ differs). **▤ Graph** opens the clip in the review area: waveform with the coloured sounds, each sound
on its own (mini graph and play button), the whole word (play + graph) and the spectrogram. The same **▤ Graph** button exists on your accepted takes
(boundaries are the ones saved with the take). Close it with **✕ Close**; nothing is saved from a reference clip. Words with no generated clip say so. The panel only exists when `--reference-dir` is given.

Optional `--aligner-url http://host.docker.internal:8790` uses the aligner service (2.4). Without it the recorder uses spectral first guesses.

### 2.2 Rebuild the lexicon (about 10-15 minutes, CPU)

```powershell
cd D:\Apps\Sound2Word; $raw='data\private\dictionary\raw'; $env:PYTHONIOENCODING='utf-8'
& data\private\venv_mlphon\Scripts\python.exe tools\s2w_lexicon.py `
  --source "$raw\datuk\files\datuk=Datuk:ODbL" --source "$raw\enml\files\enml=Olam-ENML:ODbL" `
  --source "$raw\ekkurup\ekkurup.yml=EK-Kurup:CC-BY-SA-4.0" --out data\private\dictionary\lexicon.csv
```

Licences: ODbL and CC BY-SA 4.0 are share-alike. Keep the combined file local (git-ignored). Entries are unverified suggestions.

### 2.3 Generate synthetic reference clips and score them

LFM (runs in Rootit's `.venv-indic`; extra packages come from `tts\pkgs` via `PYTHONPATH`):

```powershell
$t='D:\Apps\Sound2Word\data\private\tts'; $env:PYTHONPATH="$t\pkgs;$t\VyvoTTS"; $env:PYTHONIOENCODING='utf-8'
& 'D:\Apps\Rootit Voice\backend\.venv-indic\Scripts\python.exe' D:\Apps\Sound2Word\tools\model_eval\gen_lfm.py "$t\lfm_first10" 10        # first N lexicon words
& 'D:\Apps\Rootit Voice\backend\.venv-indic\Scripts\python.exe' D:\Apps\Sound2Word\tools\model_eval\gen_lfm_common.py "$t\lfm_common10"   # 10 everyday words
Remove-Item Env:PYTHONPATH
```

Indic Parler-TTS (own env; needs the Hugging Face login, section 5):

```powershell
$t='D:\Apps\Sound2Word\data\private\tts'; $env:PYTHONIOENCODING='utf-8'; $env:HF_HUB_DISABLE_SYMLINKS_WARNING='1'
& "$t\venv_parler\Scripts\python.exe" D:\Apps\Sound2Word\tools\model_eval\gen_parler.py "$t\parler_cap_dot"       # 2 voices x 2 sets (length cap + full stop)
& "$t\venv_parler\Scripts\python.exe" D:\Apps\Sound2Word\tools\model_eval\trim_clips.py "$t\parler_cap_dot" "$t\parler_cap_dot_trim200" 200   # keep only the first word
```

Score any generated folder (round trip: `indic-core` transcribes each clip, compare with the intended word; writes `meta_scored.json`):

```powershell
& 'D:\Apps\Rootit Voice\backend\.venv-indic\Scripts\python.exe' D:\Apps\Sound2Word\tools\model_eval\roundtrip.py "$t\parler\common10_anjali"
```

### 2.4 Aligner service (optional; places the word start/end with a Whisper-family model)

```powershell
& 'D:\Apps\Rootit Voice\backend\.venv-py310\Scripts\python.exe' D:\Apps\Sound2Word\tools\s2w_aligner_service.py --model vegam-ml --device cuda --port 8790
```

Port 8790 (8780 is Rootit's API). Then start the recorder with `--aligner-url http://host.docker.internal:8790`.
Not yet validated on real isolated Malayalam words: measure against hand-corrected takes before trusting it.

### 2.5 Benchmarks used for the comparisons

`tools\model_eval\bench.py MODEL DEVICE WAV...` (speed/memory), `cmp.py MODEL DEVICE WAV OUT.json [lang]` (transcribe),
`score.py` (WER/CER and line timing vs a lyrics reference; the paths and lyrics inside are for the 2026-10-13 song test only).

## 3. Measured results (2026-10-13, RTX 2060 SUPER 8 GB, one word at a time, no batching)

**Recognition on 60 s of a sung track vs its lyrics** (46 words): `indic-core` WER 48% / CER 17% (RTF 0.17); `vegam-ml` 78% / 43%
(RTF 0.67); `large-v3-turbo` 100% / 62% (RTF 0.24). Only the Whisper family returns timestamps; line starts were off by about 1 s at best.

**TTS, scored by `indic-core` round trip** (10 words per set):

| Model / voice | Everyday words: exact | Rare lexicon words: exact | Mean gen time | Mean clip | Notes |
|---|---|---|---|---|---|
| LFM Malayalam v0.1 (single voice) | **9/10** (CER 3%) | 2/10 (CER 35%) | 2.4 s/word | 0.54 s | tidy short clips, 24 kHz |
| Indic Parler-TTS, Anjali | 3/10 (but 9/10 start with the right word) | 4/10 | 6.1 s/word | 1.7 s | often repeats the word or adds babble |
| Indic Parler-TTS, Harish | 0/10 (9/10 start with the right word) | 2/10 | 11.6 s/word | 3.4 s | long runaway tails |

On this data **LFM is better and faster**. Parler needs tail trimming / stop control before it is usable. These are single runs on
20 words, a proxy test: listen before relying on any clip.

## 4. Time and size to cover the whole dictionary (257,684 words)

Measured speeds, single word at a time (batching could be several times faster but is not measured):

| | per word | whole lexicon | audio on disk (16-bit WAV, as generated) |
|---|---|---|---|
| LFM, 1 voice | 2.4 s | **about 170 h** | **about 6 GB** (24 kHz) |
| Parler, Anjali | 6.1 s | about 440 h | about 36 GB (44.1 kHz, untrimmed) |
| Parler, Harish | 11.6 s | about 830 h | about 73 GB (untrimmed) |
| Round-trip check with `indic-core` | about 0.3 s | about 21 h extra | n/a |

Voice labels are small: per word the sound units, boundaries, heard text and match flag are about 0.3 KB, so **about 80-100 MB** of CSV/JSON
for the whole lexicon. Stored audio features are larger: log-mel at fp16 is about 10 KB per word (about 2.6 GB for the lexicon).
Compression helps (FLAC roughly halves WAV). Practical advice: generate the few thousand everyday words first (LFM: about 2.4 s each).

## 5. Uninstall, step by step

1. Stop the recorder: `docker rm -f s2w-rec`.
2. Delete the generated and tooling folders (PowerShell):
   ```powershell
   Remove-Item -Recurse -Force D:\Apps\Sound2Word\data\private\tts            # envs, clones, generated clips (5+ GB)
   Remove-Item -Recurse -Force D:\Apps\Sound2Word\data\private\venv_mlphon
   Remove-Item -Recurse -Force D:\Apps\Sound2Word\data\private\dictionary     # raw downloads + lexicon.csv
   ```
   (Keep `data\private\voice_demo` if you want your list and takes.)
3. Delete the downloaded models from the Hugging Face cache (do **not** touch `faster-whisper-large-v3*` if Rootit uses them):
   ```powershell
   $h="$env:USERPROFILE\.cache\huggingface\hub"
   Remove-Item -Recurse -Force "$h\models--ai4bharat--indic-parler-tts","$h\models--Praha-Labs--LFM-MALAYALAM-TTS-v0.1","$h\models--hubertsiuzdak--snac_24khz","$h\models--google--flan-t5-large"
   ```
4. Remove the Hugging Face login: `& 'D:\Apps\Rootit Voice\backend\.venv-indic\Scripts\python.exe' -c "from huggingface_hub import logout; logout()"`
   then **revoke the token** at https://huggingface.co/settings/tokens and remove the git credential if you do not want it
   (`git credential-manager erase` / Windows Credential Manager entry `git:https://huggingface.co`).
5. Remove the Docker images if wanted: `docker rmi mcr.microsoft.com/playwright:v1.49.0-noble ubuntu:24.04`.
6. Revert the code: `git revert` the commits touching `tools/s2w_recorder.py`, `tools/s2w_lexicon.py`, `tools/s2w_aligner_service.py`,
   `tools/model_eval/` (or just stop using the flags; all of it is optional).

## 6. Known pitfalls (so you do not rediscover them)

- Parler env pins: `torch 2.4.1+cu121`, `torchaudio 2.4.1+cu121` (the PyPI default 2.11 breaks), `numpy 1.26.4`, `scipy 1.13.1`,
  `scikit-learn 1.5.2`, `numba 0.60.0`, `transformers 4.46.1`. Do not call `generate(..., do_sample=False)` (IndexError).
- Hugging Face downloads on Windows warn about symlinks: harmless; set `HF_HUB_DISABLE_SYMLINKS_WARNING=1`.
- `indic-core` cannot be passed to `s2w_aligner_service.py` (it is not a CTranslate2 model and has no timestamps).
- In the recorder container, `git reset --hard` would discard uncommitted edits, so the startup script copies the working tree and strips CRLF with `sed` instead.
- Gated models (`indic-parler-tts`, `vits_rasa_13`, `IndicF5`) need you to accept the conditions on the website while logged in.
- Synthetic clips and the lexicon must never be fed to the real-speech experiment (Gate B needs consenting real speakers).

## 7. Experiment: does the same sound look the same in different words? (2026-10-13)

Purpose: before trusting letter-to-segment matching, check that the segment labelled "k" in one word resembles "k" in other words.
Script: `tools/model_eval/unit_consistency.py` (run inside the Docker build tree; needs numpy). Data: 400 random lexicon words
(3-6 sounds, 2+ sources, English meaning) generated by LFM Malayalam TTS, one voice (`tools/model_eval/gen_lfm_list.py`),
scored by the `indic-core` round trip: **189 of 400 were recognised as exactly the intended word** and are the clean set.
Metric: leave-one-word-out nearest-centroid accuracy of the mean MFCC of each segment's central part (sounds with >= 8 samples).

| Segmentation | Accuracy | Balanced accuracy | Within/between distance |
|---|---|---|---|
| shuffled labels (chance) | 2.6% | | |
| S0 current recorder boundaries | 24.3% | 35.9% | 1.11 |
| S1 even split of the same region | 25.5% | 39.4% | 1.08 |
| S2x re-aligned from the data (5-fold cross-fitted) | **51.8%** | **57.1%** | 0.73 |

All 400 clips (29 sounds, 1896 samples): S0 20.0%, S1 21.0%, S2x 45.5% (balanced 32.5 / 34.1 / 50.0).

Reading: (1) the sounds DO carry label information (far above chance); (2) the current spectral boundaries are no better than an
even split; (3) re-aligning from the data roughly doubles consistency, so the boundary method is the main fault, not the generated
voice; (4) it is still weak for stops (t, th, k about 17-20%), rr, and the very frequent vowel "a" (confused with aa, r, p),
so features, labels (schwa, geminates) or the voice also contribute. One synthetic voice only: this is software validation, not
evidence about real speakers. Next: repeat on real recordings and on a second voice before changing the recorder's first guess.

### 7.1 Follow-up: "G swallows the following A" (`?word=amga`, LFM clip)

Measured on the clip (10 ms windows, RMS / low-band / zero crossings): a = 0.007-0.07 s, m (nasal murmur) = 0.07-0.20 s, g = about 30 ms
(closure about 0.20, release about 0.21-0.23), a = 0.23-0.37 s, then a faint tail to 0.46 s. The first guess gave g = 0.217-0.398 s
(contains the vowel) and a = 0.398-0.477 s (only the tail, "silent"). Cause: `suggest_edges` forbids cuts closer than 50 ms, but a stop
is about 30 ms, so the g|a cut jumps to the strongest later change. Tried: (a) unit-aware minimum spacing + soft maximum for stops
(`suggest_edges(..., units=...)`, experimental, OFF): consistency 24% -> 34% but `amga` became a|m 60 ms|g 80 ms|a 260 ms and a
tone-change test failed; (b) data-driven re-alignment (S2): 2x consistency overall but also wrong on this clip (g covers the nasal).
Conclusion: spectral-flux peaks cannot tell a nasal murmur from a stop closure. Needed next: hand-corrected boundaries as ground
truth (about 20-30 words), then pick the method by measured error in ms. Meanwhile drag the g|a handle to about 0.235 s.

### 7.2 Parler kept talking after the word: fixed with a length cap + full stop (2026-10-13)

Symptom (Harish, "അംഗകം"): the recogniser heard `അംഗകം അംഗകർമ്മം ഉഴ ഉഴറൽ`, so the clip could never match. Cause: Parler-TTS is
autoregressive and nothing told it to stop. Fix (`tools/model_eval/gen_parler.py`, variant `cap_dot`, now the default): limit
`max_new_tokens` to about 0.5 s + 0.22 s per sound (86 frames/s) and append "." to the word. Same 20 words x 2 voices, `indic-core` round trip:

| Variant | exact (of 40) | corpus CER per set (first10 A / first10 H / common10 A / common10 H) |
|---|---|---|
| base (old) | 9 | 1.04 / 3.69 / 0.86 / 5.69 |
| cap only | 16 | 0.42 / 0.81 / 0.20 / 1.00 |
| **cap + full stop** | **24** | **0.21 / 0.39 / 0.11 / 0.43** |

Parler Anjali with `cap_dot` is now at least as good as LFM on rare words (6/10 vs 2/10) and close on everyday words (9/10 vs 9/10, CER 0.11 vs 0.03).
Harish is still weaker (3/10 and 6/10). Output folders: `data\private\tts\parler_cap_dot\<set>_<voice>\` (mounted as `/ref/parler_cap_dot`).

### 7.3 Cutting each clip at the first pause (2026-10-13)

Even with the cap and full stop, a slow voice can add a second word ("അംഗകം ഒരു"). `tools/model_eval/trim_clips.py` keeps only the first
spoken word: it cuts at the first pause of at least GAP ms after speech starts (energy below 4% of peak). Same 40 clips, `indic-core` round trip:

| Pipeline | exact (of 40) | first10 Anjali / Harish | common10 Anjali / Harish |
|---|---|---|---|
| base (no cap) | 9 | 4 / 2 | 3 / 0 |
| cap + full stop | 24 | 6 / 3 | 9 / 6 |
| + trim at 120 ms pause | 27 | 6 / 4 | 9 / 8 |
| **+ trim at 200 ms pause** | **29** | 6 / 4 | **10 / 9** |
| + trim at 300 ms pause | 29 | 6 / 5 | 10 / 8 |

The 200 ms gap was chosen on these same 40 clips, so expect slightly less on new words. For comparison LFM scored 9/10 (everyday) and
2/10 (rare). Recorder reference folder is now `parler_cap_dot_trim200` (older runs stay on disk but are no longer mounted).
