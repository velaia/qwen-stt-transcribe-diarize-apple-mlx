// Maps bare word tokens (words[].text, no punctuation) onto the punctuated
// segment text, in order, via case-insensitive forward search — mirrors
// qstt.pipeline._map_to_source (see docs/webui-contract.md, "Result JSON").
//
// Punctuation stays attached to the PRECEDING word: a matched token's span
// is extended to swallow any immediately-following run of non-alphanumeric,
// non-whitespace characters (",", ".", "?!", closing quotes, ...). A token
// only matches as a whole word; one that cannot be found forward of the
// cursor is skipped entirely — it gets no span and the cursor does not move.

import type { Word } from './types';

const WORD_CHAR = /[\p{L}\p{N}]/u;

// Plain substring search put "the" inside "then" and "a" inside "Astra",
// splitting the word and shifting every later span in the segment
// (16 of 24,525 words on a 2.5-hour podcast).
function indexOfWholeWord(haystack: string, needle: string, from: number): number {
  let idx = haystack.indexOf(needle, from);
  while (idx !== -1) {
    const before = idx > 0 ? haystack[idx - 1] : '';
    const after = haystack[idx + needle.length] ?? '';
    if (!WORD_CHAR.test(before) && !WORD_CHAR.test(after)) return idx;
    idx = haystack.indexOf(needle, idx + 1);
  }
  return -1;
}

export interface TextChunk {
  kind: 'gap';
  text: string;
}

export interface WordChunk {
  kind: 'word';
  text: string;
  wordIndex: number;
  start: number;
  end: number;
}

export type Chunk = TextChunk | WordChunk;

const TRAILING_PUNCT = /[^\s\p{L}\p{N}]/u;

export function mapWordsToChunks(
  text: string,
  words: Word[],
  segStart: number,
  segEnd: number
): Chunk[] {
  if (!words || words.length === 0) {
    if (!text) return [];
    return [{ kind: 'word', text, wordIndex: -1, start: segStart, end: segEnd }];
  }

  const lower = text.toLowerCase();
  const chunks: Chunk[] = [];
  let cursor = 0;

  for (let i = 0; i < words.length; i++) {
    const token = words[i].text;
    if (!token) continue;
    const needle = token.toLowerCase();
    const idx = indexOfWholeWord(lower, needle, cursor);
    if (idx === -1) continue; // unmatched token: skip, cursor unchanged

    if (idx > cursor) {
      chunks.push({ kind: 'gap', text: text.slice(cursor, idx) });
    }

    let matchEnd = idx + token.length;
    while (matchEnd < text.length && TRAILING_PUNCT.test(text[matchEnd])) {
      matchEnd++;
    }

    chunks.push({
      kind: 'word',
      text: text.slice(idx, matchEnd),
      wordIndex: i,
      start: words[i].start,
      end: words[i].end
    });
    cursor = matchEnd;
  }

  if (cursor < text.length) {
    chunks.push({ kind: 'gap', text: text.slice(cursor) });
  }

  return chunks;
}
