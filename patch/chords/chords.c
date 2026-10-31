/*
 * chords: more MIDI chords, inversions first (Polyend Tracker+ firmware 1.2.0).
 *
 * The MIDI Chord FX (0, type 14) stores an index 0..29 in the step's FX value byte. Stock
 * firmware plays index v from a table of 30 interval masks at 0x60039bfc: bit i set means
 * "also send the step's note + i" (bit 0, the note itself, is always set). Values 30..255
 * are free: stock firmware plays them as the note alone (its guards are cmp #0x1d) and
 * shows odd text for them. This patch gives values 30..CH_LAST chords of their own,
 * ch_library below: first the inversions of the major and minor triads and of the
 * maj7, min7 and dom7 chords, then the other inversions of stock chords, then every
 * other chord of one to three extra notes within an octave. CH_LAST + 1..255 stay
 * free; they play as in stock firmware.
 *
 * Playing them. A library chord is played as one stock note per chord note: the hooks
 * call the stock function once per interval, in ascending order (the note first), on a
 * copy of the record whose note is raised by the interval and whose chord value is 0.
 * Value 0 is the note alone, so the stock code sends each note with its own velocity,
 * channel, synth and render handling, exactly as it sends the notes of a stock chord
 * (FINDINGS section 32). Notes above 127 are skipped, as stock does. The copy keeps
 * its Chord type, so other patches still see a chord.
 *  - Note-ons: seq_trigger_note (ITCM 0x149c) has three callers, the ITCM BLs at 0x2fd8
 *    (the step's note; legato hooks it too), 0x285c (Roll's repeats) and 0x22f8
 *    (performance mode's step repeat).
 *  - Note-offs: seq_note_off_q (ITCM 0x11a4) sends a chord record's offs through the
 *    ITCM veneer 0x1f698 to seq_chord_note_off_q (0x600ac508), whose address is the
 *    word at 0x601acd80 in flash. Every note-off reaches it: the next note, OFF, CUT,
 *    FADE, the gate end, mute and stop. The offs come from the record stored at the
 *    note-on, and the library is constant, so they always match the note-ons.
 * Both apply stock firmware's rule: a MIDI instrument or synth (48..66), FX1 if it is
 * Chord, else FX2, and not when the other column is Arp (21).
 *
 * The Arp (A). At an arp's start the step's chord value goes into the track's arp state
 * (state +0x44); arp_next_note_q (ITCM 0xb74) then takes the chord's mask from the stock
 * table, through its literal at ITCM 0xcc0, and plays chromatic for values above 29.
 * Around its two callers (ITCM 0x2428, 0x32ce), a library chord borrows stock entry 1:
 * the hook points the literal at a 2-entry table on its stack whose entry 1 is the
 * library chord's mask, sets the state's value to 1, calls arp_next_note_q, and puts
 * both back. The stock code then does all the rest (direction, random, octave folding).
 * Only the audio interrupt calls it, so nothing else sees the borrowed literal. The
 * literal is data in ITCM, which the MPU maps read-write (FINDINGS section 8).
 *
 * Entering and showing them. The encoder edit of FX values (seq_change_selection_fx_value,
 * 0x600af0f8) sets a cell to min(max, value + delta), the max from fx_max through the
 * call at 0x600af1b2; mt_ch_cell_max gives Chord CH_LAST there, so turning right from
 * 29 goes on into the library. Pads, Fill, random fill and Random FX keep the stock
 * 0..29. The pattern grid gets each FX value's text from fx_value_text_cell (0x600afdc8,
 * called at 0x600df748 for FX1 and 0x600df77c for FX2); the hooks there write a library
 * chord's intervals the way stock labels its chords: hex digits, right-aligned in 3
 * characters (" 59", "368").
 *
 * Shared hooks go through chain words (mt_ch_next_*) that tools/patch_ptf.py fills with
 * what the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef CHORDS_VERSION
#define CHORDS_VERSION "ch2"
#endif

/* ---- firmware 1.2.0 ------------------------------------------------------------------ */
#define SEQ_PATTERN         0x14u           /* the playing pattern's buffer (.mtp layout) */
#define TRACKS              16u
#define REC_SIZE            7u              /* note, instrument, FX1 type, value, FX2 type, value, volume */
#define REC_NOTE            0u
#define REC_INSTR           1u
#define REC_FX1             2u
#define REC_FX2             4u
#define INSTR_FIRST         48u             /* MIDI channel 1 */
#define INSTR_LAST          66u             /* synth slot S3 */
#define FX_CHORD            14u
#define FX_ARP              21u
#define ARP_CHORD           0x44u           /* arp state: the step's chord value */
#define ARP_TABLE_LITERAL   0x00000CC0u     /* ITCM: arp_next_note_q's pointer to the chord masks */
#define NOTE_MAX            127

/* pattern: track t's step s, FX column c (0 FX1, 1 FX2) */
#define PAT_FX(t, s, c)     (0x1Fu + 0x301u * (t) + 6u * (s) + 2u * (c))  /* type; the value follows */

typedef void (*trigger_fn)(void *seq, uint32_t track, const uint8_t *rec);
typedef void (*chord_off_fn)(void *seq, uint32_t track, const uint8_t *rec, int32_t mode);
typedef uint32_t (*arp_fn)(void *seq, uint8_t *state);
typedef int32_t (*max_fn)(void *selection, uint32_t type, int32_t value);
typedef int32_t (*cell_text_fn)(void *seq, char *buf, uint32_t size, uint32_t column, uint32_t track,
                                int32_t step);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_ch_next_trigger_a);           /* seq_trigger_note, ITCM 0x149d (from 0x22f8) */
CHAIN_WORD(mt_ch_next_trigger_b);           /* ... from 0x285c */
CHAIN_WORD(mt_ch_next_trigger_c);           /* ... from 0x2fd8 */
CHAIN_WORD(mt_ch_next_arp_a);               /* arp_next_note_q, ITCM 0xb75 (from 0x2428) */
CHAIN_WORD(mt_ch_next_arp_b);               /* ... from 0x32ce, the arp's start */
CHAIN_WORD(mt_ch_next_note_off);            /* seq_chord_note_off_q 0x600ac509 */
CHAIN_WORD(mt_ch_next_cell_max);            /* fx_max (veneer 0x600af2c9) */
CHAIN_WORD(mt_ch_next_fx1_text);            /* fx_value_text_cell 0x600afdc9 */
CHAIN_WORD(mt_ch_next_fx2_text);            /* fx_value_text_cell 0x600afdc9 */

/* ---- the library ------------------------------------------------------------------- */

/* Ids are stored in saved patterns: an id never changes meaning once released. */
#define CH_FIRST            30u             /* after the stock chords 0..29 */
#define CH_LAST             236u            /* CH_LAST + 1..255: free, played as stock */
#define CH_COUNT            (CH_LAST - CH_FIRST + 1u)

/* the note (bit 0) and one to three intervals above it, in semitones */
#define M1(a)               (uint16_t)(1u | 1u << (a))
#define M2(a, b)            (uint16_t)(M1(a) | 1u << (b))
#define M3(a, b, c)         (uint16_t)(M2(a, b) | 1u << (c))

static const uint16_t ch_library[CH_COUNT] = {
    M2(3, 8),          /*  30 " 38" major, 1st inversion */
    M2(5, 9),          /*  31 " 59" major, 2nd inversion */
    M2(4, 9),          /*  32 " 49" minor, 1st inversion */
    M2(5, 8),          /*  33 " 58" minor, 2nd inversion */
    M3(3, 7, 8),       /*  34 "378" maj7, 1st inversion (= stock 378) */
    M3(4, 5, 9),       /*  35 "459" maj7, 2nd inversion */
    M3(1, 5, 8),       /*  36 "158" maj7, 3rd inversion */
    M3(4, 7, 9),       /*  37 "479" min7, 1st inversion (= stock 479) */
    M3(3, 5, 8),       /*  38 "358" min7, 2nd inversion */
    M3(2, 5, 9),       /*  39 "259" min7, 3rd inversion */
    M3(3, 6, 8),       /*  40 "368" dom7, 1st inversion */
    M3(3, 5, 9),       /*  41 "359" dom7, 2nd inversion */
    M3(2, 6, 9),       /*  42 "269" dom7, 3rd inversion */
    /* the other inversions of stock chords */
    M2(3, 9),          /*  43 " 39" diminished, 1st inversion */
    M2(6, 9),          /*  44 " 69" diminished, 2nd inversion */
    M2(6, 0xA),        /*  45 " 6A" sus2#5, 1st inversion */
    M2(4, 6),          /*  46 " 46" sus2#5, 2nd inversion */
    M2(1, 6),          /*  47 " 16" sus#4, 1st inversion */
    M2(5, 0xB),        /*  48 " 5B" sus#4, 2nd inversion */
    M3(4, 6, 9),       /*  49 "469" m7b5, 1st inversion */
    M3(2, 5, 8),       /*  50 "258" m7b5, 2nd inversion */
    M3(4, 5, 8),       /*  51 "458" mMaj7, 1st inversion */
    M3(1, 4, 8),       /*  52 "148" mMaj7, 2nd inversion */
    M3(4, 6, 8),       /*  53 "468" aug7, 1st inversion */
    M3(2, 4, 8),       /*  54 "248" aug7, 2nd inversion */
    M3(2, 6, 0xA),     /*  55 "26A" aug7, 3rd inversion */
    M3(4, 7, 8),       /*  56 "478" augMaj7, 1st inversion */
    M3(3, 4, 8),       /*  57 "348" augMaj7, 2nd inversion */
    M3(1, 5, 9),       /*  58 "159" augMaj7, 3rd inversion */
    M3(2, 5, 7),       /*  59 "257" sus2add6 / 7sus4, 1st inversion */
    M3(3, 5, 0xA),     /*  60 "35A" sus2add6 / 7sus4, 2nd inversion */
    M3(5, 8, 0xA),     /*  61 "58A" sus2b7, 1st inversion */
    M3(3, 5, 7),       /*  62 "357" sus2b7, 2nd inversion */
    M3(2, 4, 9),       /*  63 "249" sus2b7, 3rd inversion */
    M3(5, 9, 0xA),     /*  64 "59A" sus2maj7, 1st inversion */
    M3(4, 5, 7),       /*  65 "457" sus2maj7, 2nd inversion */
    M3(1, 3, 8),       /*  66 "138" sus2maj7, 3rd inversion */
    M3(2, 6, 7),       /*  67 "267" sus4maj7, 1st inversion */
    M3(4, 5, 0xA),     /*  68 "45A" sus4maj7, 2nd inversion */
    M3(1, 6, 8),       /*  69 "168" sus4maj7, 3rd inversion */
    /* every other chord within an octave: one, two, then three intervals, in digit order */
    /*  70 */ M1(1), M1(2), M1(3), M1(4), M1(5), M1(6),
    /*  76 */ M1(7), M1(8), M1(9), M1(0xA), M1(0xB), M2(1, 2),
    /*  82 */ M2(1, 3), M2(1, 4), M2(1, 5), M2(1, 7), M2(1, 8), M2(1, 9),
    /*  88 */ M2(1, 0xA), M2(1, 0xB), M2(2, 3), M2(2, 4), M2(2, 5), M2(2, 6),
    /*  94 */ M2(2, 9), M2(2, 0xA), M2(2, 0xB), M2(3, 4), M2(3, 5), M2(3, 0xA),
    /* 100 */ M2(3, 0xB), M2(4, 5), M2(4, 0xA), M2(4, 0xB), M2(5, 6), M2(6, 8),
    /* 106 */ M2(6, 0xB), M2(7, 8), M2(7, 9), M2(7, 0xA), M2(7, 0xB), M2(8, 9),
    /* 112 */ M2(8, 0xA), M2(8, 0xB), M2(9, 0xA), M2(9, 0xB), M2(0xA, 0xB), M3(1, 2, 3),
    /* 118 */ M3(1, 2, 4), M3(1, 2, 5), M3(1, 2, 6), M3(1, 2, 7), M3(1, 2, 8), M3(1, 2, 9),
    /* 124 */ M3(1, 2, 0xA), M3(1, 2, 0xB), M3(1, 3, 4), M3(1, 3, 5), M3(1, 3, 6), M3(1, 3, 7),
    /* 130 */ M3(1, 3, 9), M3(1, 3, 0xA), M3(1, 3, 0xB), M3(1, 4, 5), M3(1, 4, 6), M3(1, 4, 7),
    /* 136 */ M3(1, 4, 9), M3(1, 4, 0xA), M3(1, 4, 0xB), M3(1, 5, 6), M3(1, 5, 7), M3(1, 5, 0xA),
    /* 142 */ M3(1, 5, 0xB), M3(1, 6, 7), M3(1, 6, 9), M3(1, 6, 0xA), M3(1, 6, 0xB), M3(1, 7, 8),
    /* 148 */ M3(1, 7, 9), M3(1, 7, 0xA), M3(1, 7, 0xB), M3(1, 8, 9), M3(1, 8, 0xA), M3(1, 8, 0xB),
    /* 154 */ M3(1, 9, 0xA), M3(1, 9, 0xB), M3(1, 0xA, 0xB), M3(2, 3, 4), M3(2, 3, 5), M3(2, 3, 6),
    /* 160 */ M3(2, 3, 7), M3(2, 3, 8), M3(2, 3, 9), M3(2, 3, 0xA), M3(2, 3, 0xB), M3(2, 4, 5),
    /* 166 */ M3(2, 4, 6), M3(2, 4, 7), M3(2, 4, 0xA), M3(2, 4, 0xB), M3(2, 5, 6), M3(2, 5, 0xA),
    /* 172 */ M3(2, 5, 0xB), M3(2, 6, 8), M3(2, 6, 0xB), M3(2, 7, 8), M3(2, 8, 9), M3(2, 8, 0xA),
    /* 178 */ M3(2, 8, 0xB), M3(2, 9, 0xA), M3(2, 9, 0xB), M3(2, 0xA, 0xB), M3(3, 4, 5), M3(3, 4, 6),
    /* 184 */ M3(3, 4, 7), M3(3, 4, 9), M3(3, 4, 0xA), M3(3, 4, 0xB), M3(3, 5, 6), M3(3, 5, 0xB),
    /* 190 */ M3(3, 6, 7), M3(3, 6, 0xB), M3(3, 8, 9), M3(3, 8, 0xA), M3(3, 8, 0xB), M3(3, 9, 0xA),
    /* 196 */ M3(3, 9, 0xB), M3(3, 0xA, 0xB), M3(4, 5, 6), M3(4, 5, 0xB), M3(4, 6, 7), M3(4, 6, 0xA),
    /* 202 */ M3(4, 6, 0xB), M3(4, 9, 0xA), M3(4, 9, 0xB), M3(4, 0xA, 0xB), M3(5, 6, 7), M3(5, 6, 8),
    /* 208 */ M3(5, 6, 9), M3(5, 6, 0xA), M3(5, 6, 0xB), M3(5, 7, 8), M3(5, 7, 9), M3(5, 8, 9),
    /* 214 */ M3(5, 8, 0xB), M3(5, 9, 0xB), M3(5, 0xA, 0xB), M3(6, 7, 8), M3(6, 7, 9), M3(6, 7, 0xA),
    /* 220 */ M3(6, 7, 0xB), M3(6, 8, 9), M3(6, 8, 0xA), M3(6, 8, 0xB), M3(6, 9, 0xA), M3(6, 9, 0xB),
    /* 226 */ M3(6, 0xA, 0xB), M3(7, 8, 9), M3(7, 8, 0xA), M3(7, 8, 0xB), M3(7, 9, 0xA), M3(7, 9, 0xB),
    /* 232 */ M3(7, 0xA, 0xB), M3(8, 9, 0xA), M3(8, 9, 0xB), M3(8, 0xA, 0xB), M3(9, 0xA, 0xB),
};

/* Inlined: these run for every MIDI and synth note (patch cost, test_chords.py). */
#define CH_INLINE static inline __attribute__((always_inline))

/* The record offset of a library chord's value (3 or 5) under stock firmware's chord
 * rule, or 0 when stock firmware plays the record (no Chord, Chord with Arp, not a
 * MIDI or synth note, or a value outside the library). */
CH_INLINE uint32_t ch_value_at(const uint8_t *rec)
{
    uint32_t instr = rec[REC_INSTR], at;
    if (instr < INSTR_FIRST || instr > INSTR_LAST)
        return 0;
    if (rec[REC_FX1] == FX_CHORD) {
        if (rec[REC_FX2] == FX_ARP)
            return 0;
        at = REC_FX1 + 1u;
    } else if (rec[REC_FX2] == FX_CHORD) {
        if (rec[REC_FX1] == FX_ARP)
            return 0;
        at = REC_FX2 + 1u;
    } else {
        return 0;
    }
    uint32_t v = rec[at];
    return v >= CH_FIRST && v <= CH_LAST ? at : 0;
}

/* copy = rec with the chord value 0; returns the chord's mask */
CH_INLINE uint32_t ch_start(const uint8_t *rec, uint32_t at, uint8_t *copy)
{
    copy[0] = rec[0], copy[1] = rec[1], copy[2] = rec[2], copy[3] = rec[3];
    copy[4] = rec[4], copy[5] = rec[5], copy[6] = rec[6];
    copy[at] = 0;
    return ch_library[rec[at] - CH_FIRST];
}

/* The chord's next note, the lowest left in *mask (which loses it), as the copy's note.
 * 0 when no note within 0..127 is left. */
CH_INLINE int ch_next_note(uint32_t *mask, const uint8_t *rec, uint8_t *copy)
{
    while (*mask) {
        int32_t note = (int8_t)rec[REC_NOTE] + __builtin_ctz(*mask);
        *mask &= *mask - 1u;
        if (note >= 0 && note <= NOTE_MAX) {
            copy[REC_NOTE] = (uint8_t)note;
            return 1;
        }
    }
    return 0;
}

/* ---- playing (audio interrupt) ------------------------------------------------------ */

static void ch_trigger(uint32_t next, void *seq, uint32_t track, const uint8_t *rec)
{
    uint32_t at = ch_value_at(rec);
    if (at == 0u) {
        ((trigger_fn)next)(seq, track, rec);
        return;
    }
    uint8_t copy[REC_SIZE];
    uint32_t mask = ch_start(rec, at, copy);
    while (ch_next_note(&mask, rec, copy))
        ((trigger_fn)next)(seq, track, copy);
}

/* ITCM 0x22f8, 0x285c, 0x2fd8: "bl seq_trigger_note(seq, track, rec)" -> stub -> here */
void mt_ch_trigger_a(void *seq, uint32_t track, const uint8_t *rec)
{
    ch_trigger(mt_ch_next_trigger_a, seq, track, rec);
}

void mt_ch_trigger_b(void *seq, uint32_t track, const uint8_t *rec)
{
    ch_trigger(mt_ch_next_trigger_b, seq, track, rec);
}

void mt_ch_trigger_c(void *seq, uint32_t track, const uint8_t *rec)
{
    ch_trigger(mt_ch_next_trigger_c, seq, track, rec);
}

/* the word at 0x601acd80 (chained): the ITCM veneer 0x1f698's jump to
 * seq_chord_note_off_q(seq, track, rec, mode), from seq_note_off_q for chord records */
void mt_ch_note_off(void *seq, uint32_t track, const uint8_t *rec, int32_t mode)
{
    uint32_t at = ch_value_at(rec);
    if (at == 0u) {
        ((chord_off_fn)mt_ch_next_note_off)(seq, track, rec, mode);
        return;
    }
    uint8_t copy[REC_SIZE];
    uint32_t mask = ch_start(rec, at, copy);
    while (ch_next_note(&mask, rec, copy))
        ((chord_off_fn)mt_ch_next_note_off)(seq, track, copy, mode);
}

/* The arp's next note for a library chord: arp_next_note_q with the library chord's mask
 * as stock chord 1 (see the top). Anything else goes on unchanged. */
static uint32_t ch_arp(uint32_t next, void *seq, uint8_t *state)
{
    uint32_t v = state[ARP_CHORD];
    if (v < CH_FIRST || v > CH_LAST)
        return ((arp_fn)next)(seq, state);
    uint16_t table[2];
    table[0] = 0;
    table[1] = ch_library[v - CH_FIRST];
    uint32_t at = ARP_TABLE_LITERAL;
    __asm__("" : "+r"(at));                 /* GCC takes addresses below 4 KB for null + offset */
    volatile uint32_t *literal = (volatile uint32_t *)at;
    uint32_t stock = *literal;
    *literal = (uint32_t)table;
    state[ARP_CHORD] = 1;
    uint32_t note = ((arp_fn)next)(seq, state);
    state[ARP_CHORD] = (uint8_t)v;
    *literal = stock;
    return note;
}

/* ITCM 0x2428, 0x32ce: "bl arp_next_note_q(seq, state)" -> stub -> here */
uint32_t mt_ch_arp_a(void *seq, uint8_t *state)
{
    return ch_arp(mt_ch_next_arp_a, seq, state);
}

uint32_t mt_ch_arp_b(void *seq, uint8_t *state)
{
    return ch_arp(mt_ch_next_arp_b, seq, state);
}

/* ---- entering and showing chords (main loop) ------------------------------------------ */

/* replaces "bl fx_max" (veneer 0x600af2c8) at 0x600af1b2 in seq_change_selection_fx_value
 * (chained): a cell's highest value for an encoder turn; the cell gets min(it, value) */
int32_t mt_ch_cell_max(void *selection, uint32_t type, int32_t value)
{
    if (type == FX_CHORD)
        return CH_LAST;
    return ((max_fn)mt_ch_next_cell_max)(selection, type, value);
}

/* A library chord's text for fx_value_text_cell(seq, buf, size, column, track, step), or
 * 0: its intervals in hex, right-aligned in 3 characters, as stock labels its chords. */
static int ch_cell_text(const void *seq, char *buf, uint32_t size, uint32_t column, uint32_t track, int32_t step)
{
    int32_t s = (int8_t)step;               /* as fx_value_text_cell reads them: bytes, the step signed */
    uint32_t t = track & 0xFFu, c = column & 0xFFu;
    if (s < 0 || t >= TRACKS || c > 1u || size < 4u)
        return 0;
    const uint8_t *pattern = *(const uint8_t *const *)((const uint8_t *)seq + SEQ_PATTERN);
    const uint8_t *fx = pattern + PAT_FX(t, (uint32_t)s, c);
    if (fx[0] != FX_CHORD || fx[1] < CH_FIRST || fx[1] > CH_LAST)
        return 0;
    uint32_t mask = ch_library[fx[1] - CH_FIRST], n = 3;
    buf[0] = buf[1] = buf[2] = ' ';
    buf[3] = 0;
    for (uint32_t i = 11; i >= 1u && n > 0u; i--)
        if (mask >> i & 1u)
            buf[--n] = (char)(i < 10u ? '0' + i : 'A' + i - 10u);
    return 1;
}

/* replace "bl fx_value_text_cell" at 0x600df748 (FX1) and 0x600df77c (FX2) in the pattern
 * grid's row fill (chained); the grid ignores the result */
int32_t mt_ch_fx1_text(void *seq, char *buf, uint32_t size, uint32_t column, uint32_t track, int32_t step)
{
    if (ch_cell_text(seq, buf, size, column, track, step))
        return 3;
    return ((cell_text_fn)mt_ch_next_fx1_text)(seq, buf, size, column, track, step);
}

int32_t mt_ch_fx2_text(void *seq, char *buf, uint32_t size, uint32_t column, uint32_t track, int32_t step)
{
    if (ch_cell_text(seq, buf, size, column, track, step))
        return 3;
    return ((cell_text_fn)mt_ch_next_fx2_text)(seq, buf, size, column, track, step);
}

/* ---- version text: Config shows 1.2.0-<CHORDS_VERSION> -------------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" CHORDS_VERSION;
