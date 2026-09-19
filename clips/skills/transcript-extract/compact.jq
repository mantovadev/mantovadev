# compact.jq: turn whisper.cpp -ojf raw output into the compact transcript.
# Input: raw -ojf JSON (top-level "transcription" array of segments, each with
# punctuated "text" and per-token "tokens" with sub-word offsets).
# Output: { event, language, model, segments: [{i,s,e,text}], words: [{w,s,e,seg}] }
#
# Word merge: within each segment, walk tokens in order. Special tokens (text
# matching ^\[_.*\]$, e.g. "[_BEG_]", "[_TT_123]") are skipped. A token whose
# text starts with a space begins a new word; otherwise it is appended to the
# current word (this is how sub-word pieces and punctuation like "," or "'"
# reattach, e.g. "dell" + "'" + "amb" + "iente" -> "dell'ambiente"). A word's
# start is its first token's offset; its end is its last token's offset.
#
# End-time clamp: whisper word end times absorb trailing silence (some spans
# were 50+ seconds in the previous run). Each word's end is clamped to the
# earliest of: its own raw end, the next word's start, and its own start + 1.5s.
# Word timing overall (start included) is only rough (see SKILL.md's timing
# contract); segment (sentence) times are the reliable level. Consumers should
# still derive any word tail/duration from the NEXT word's start rather than
# from this clamped end time.

def round2: ((. * 100) | round) / 100;

# VAD timeline fix: with --vad, whisper-cli (1.9.4) reports segment offsets on the
# original audio timeline but token offsets on the VAD-compressed timeline (silence
# removed), so token times drift earlier and earlier (over 1100 s on a 68 min talk).
# When $vad is true, shift each segment's tokens so its first real token lands on the
# segment start. Silence removed INSIDE a segment is not recovered, so words after an
# internal pause can be early by the length of that pause.
def seg_shift(seg):
  if $vad then
    ([seg.tokens[]? | select(.text | test("^\\[_.*\\]$") | not) | .offsets.from] | first // null) as $t0
    | if $t0 == null then 0 else (seg.offsets.from - $t0) end
  else 0 end;

def merge_segment_tokens(toks; $segidx; $shift):
  (reduce toks[] as $t (
      {words: [], cur: null};
      if ($t.text | test("^\\[_.*\\]$")) then .
      elif (.cur == null) then
        .cur = {text: $t.text, s: $t.offsets.from, e: $t.offsets.to}
      elif ($t.text | startswith(" ")) then
        .words += [.cur]
        | .cur = {text: $t.text, s: $t.offsets.from, e: $t.offsets.to}
      else
        (.cur.text += $t.text)
        | (.cur.e = $t.offsets.to)
      end
    )
    | (if .cur != null then .words += [.cur] else . end)
    | .words
  )
  | map({w: (.text | gsub("^\\s+|\\s+$"; "")), s: (.s + $shift), e: (.e + $shift), seg: $segidx})
  | map(select(.w != ""))
;

(.transcription // []) as $segs
| ($segs
    | to_entries
    | map(merge_segment_tokens(.value.tokens // []; .key; seg_shift(.value)))
    | add // []
  ) as $raw_words
| ($raw_words | length) as $n
| {
    event: $event,
    language: $language,
    model: $model,
    segments: [
      $segs
      | to_entries[]
      | {
          i: .key,
          s: (.value.offsets.from / 1000.0 | round2),
          e: (.value.offsets.to / 1000.0 | round2),
          text: (.value.text | gsub("^\\s+|\\s+$"; ""))
        }
    ],
    words: [
      range(0; $n) as $idx
      | $raw_words[$idx] as $w
      | (if $idx + 1 < $n then $raw_words[$idx + 1].s else $w.e end) as $next_s
      | ([$w.e, $next_s, $w.s + 1500] | min) as $e_clamped
      | { w: $w.w, s: ($w.s / 1000.0 | round2), e: ($e_clamped / 1000.0 | round2), seg: $w.seg }
    ]
  }
