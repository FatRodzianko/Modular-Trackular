/*
 * condtrig: conditional trigs (Polyend Tracker+ firmware 1.2.0).
 *
 * The idea comes from Psygoo's Tracker+ Conditional Trigs (CT6): Chance FX values
 * above 100 hold conditions on how many times the pattern has played. This is a
 * re-implementation from that behaviour; no code is shared.
 *
 * A step with a Chance FX (C, type 4) plays when a random number 0..100 is at most the
 * value: the FX pass of seq_process_track (ITCM) calls rand_range_q(0, 100) at ITCM
 * 0x30ce and, when the number is above the value, marks the step failed (track +0x98),
 * which drops its note and all its FX. Stock values are 0..100, so 101 and up always
 * play in stock firmware. Here they are conditions on the track's pass count n
 * (0 = the first pass of the loop since playback or the pattern started):
 *   101 "1st"   n = 0
 *   102 "!1s"   n >= 1
 *   103..137    A:B for B = 2..8, A = 1..B (1:2, 2:2, 1:3, 2:3, 3:3, 1:4 ... 8:8):
 *               the step plays when n mod B = A - 1
 * mt_ct_chance replaces that rand_range_q call (through an ITCM stub that passes the
 * FX column's record and the track): for a condition it returns 0 (plays) or a number
 * above any value (fails); for anything else it calls rand_range_q as stock does.
 * Two Chance FX on one step must both pass, as in stock firmware.
 *
 * The count, per track. seq_advance_step (0x600ad010, through the word the
 * sequencer's ITCM code jumps through) moves a track to its next step. It counts the
 * steps of the track's loop in track +0x8a and sets that back to 0 when the track starts
 * its loop again, in every play mode (forward, backward, random, the pattern tables),
 * after a Break FX and at the loop's own length in performance mode: then n + 1.
 * All counts go back to 0:
 *  - when playback starts: seq_play_from (its four flash veneers, and the end of a
 *    count-in at ITCM 0x3752) asks for it, and playing going from off to on (seen at
 *    the start of each audio block, before the sequencer runs; this also covers Preview
 *    and rendering);
 *  - when the playing pattern changes (song mode moving to another pattern, a queued
 *    pattern), seen when track 16 ends a step. In song mode a pattern repeated in the
 *    next slot keeps counting, so its repeats can play different steps.
 * A track's count goes back to 0 when its own queued pattern (performance mode) starts.
 * With Variable Track Length (patch/tracklen) in the build, a track with its own length E
 * also counts each start of its own loop: a step whose position is a nonzero multiple
 * of E (track +0x88, the position that track reads mod E). mt_ct_tl_len is linked to
 * tracklen's mt_tl_len by the patcher ("links"), and is 0 without tracklen. In
 * performance mode only forward tracks count this way: the others' positions jump.
 * Only the audio interrupt changes the counts; the main loop only sets `pending`.
 *
 * Entering and showing them. The encoder edit of FX values (seq_change_selection_fx_value,
 * 0x600af0f8) sets a cell to min(max, value + delta), the max from fx_max through the
 * call at 0x600af1b2; mt_ct_cell_max gives Chance CT_LAST there, so turning right from
 * 100 steps through the conditions in order and turning left goes back. Every other way
 * of setting a value (pads, Fill, random fill, recording) keeps the stock 0..100. The
 * pattern grid gets each FX value's text from fx_value_text_cell (0x600afdc8, called at
 * 0x600df748 for FX1 and 0x600df77c for FX2); mt_ct_cell_text writes a condition's
 * name there instead of its number. No other screen shows step FX values: the Fill
 * popup's texts only get values of 0..100.
 *
 * Shared hooks go through chain words (mt_ct_next_*) that tools/patch_ptf.py fills with
 * what the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef CONDTRIG_VERSION
#define CONDTRIG_VERSION "ct2"
#endif

/* ---- firmware 1.2.0 addresses ------------------------------------------------------ */
#define FW_PROJECT_PATTERN  ((const volatile uint8_t *)0x202046C8u)  /* project +0x6c8: playing pattern */

/* sequencer object */
#define SEQ_PATTERN         0x14u           /* the playing pattern's buffer (.mtp layout) */
#define SEQ_PLAYING         0x2Au
#define SEQ_PERFORMANCE     0x1Du           /* performance mode on */
#define SEQ_TRACK(t)        (0x68u * (t))
#define TRK_STEP            0x88u           /* uint16: the track's position in the pattern */
#define TRK_LOOP_STEPS      0x8Au           /* int16: steps of the loop so far, 0 at its start */
#define TRK_PLAY_MODE       0xB9u           /* performance mode: 0 forward, else backward, random... */
#define TRK_QUEUED          0xBCu           /* int32: queued pattern (performance mode), -1 none */
#define TRACKS              16u
#define LAST_TRACK          15u

/* pattern: track t's step s, FX column c (0 FX1, 1 FX2) */
#define PAT_FX(t, s, c)     (0x1Fu + 0x301u * (t) + 6u * (s) + 2u * (c))  /* type; the value follows */
#define FX_CHANCE           4u

/* conditions: Chance FX values */
#define CT_FIRST            101u            /* 1st */
#define CT_NOT_FIRST        102u            /* !1s */
#define CT_RATIO            103u            /* 1:2, then A:B by B = 2..8 */
#define CT_LAST             137u            /* 8:8 */
#define CT_CYCLE            840u            /* lcm(2..8): counts wrap by it, keeping n mod B */
#define CT_FAIL             0x7FFFFFFF      /* above any Chance value: the step fails */

typedef int32_t (*rand_fn)(int32_t lo, int32_t hi, const uint8_t *column, uint32_t track);
typedef void (*play_from_fn)(void *seq, uint32_t step);
typedef void (*advance_fn)(void *seq, float seconds);
typedef uint32_t (*advance_step_fn)(void *seq, uint32_t track);
typedef int32_t (*max_fn)(void *selection, uint32_t type, int32_t value);
typedef uint32_t (*tl_len_fn)(const void *seq, uint32_t track);
typedef int32_t (*cell_text_fn)(void *seq, char *buf, uint32_t size, uint32_t column, uint32_t track,
                                int32_t step);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_ct_next_chance);              /* rand_range_q, ITCM 0x3af5 */
CHAIN_WORD(mt_ct_next_count_in);            /* seq_play_from, ITCM 0x1251 */
CHAIN_WORD(mt_ct_next_play_from_a);         /* seq_play_from, through veneer 0x600ac600 */
CHAIN_WORD(mt_ct_next_play_from_b);         /* ... 0x600ac690 */
CHAIN_WORD(mt_ct_next_play_from_c);         /* ... 0x600acd30 */
CHAIN_WORD(mt_ct_next_play_from_d);         /* ... 0x600ad540 */
CHAIN_WORD(mt_ct_next_advance_step);        /* seq_advance_step 0x600ad011 */
CHAIN_WORD(mt_ct_next_seq_advance);         /* seq_advance (veneer 0x600aa859) */
CHAIN_WORD(mt_ct_next_render_advance);      /* seq_advance in the render loop (veneer 0x600bc2d9) */
CHAIN_WORD(mt_ct_next_cell_max);            /* fx_max (veneer 0x600af2c9) */
CHAIN_WORD(mt_ct_next_fx1_text);            /* fx_value_text_cell 0x600afdc9 */
CHAIN_WORD(mt_ct_next_fx2_text);            /* fx_value_text_cell 0x600afdc9 */
CHAIN_WORD(mt_ct_tl_len);                   /* tracklen's mt_tl_len ("links"), 0 without it */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define CT_MAGIC            0x54434D47u     /* "GMCT" */

struct ct_state {
    uint32_t magic;
    uint16_t pass[TRACKS];                  /* n per track */
    uint8_t pending;                        /* playback (re)started: counts back to 0 */
    uint8_t was_playing;
    uint8_t pad[2];
};
static struct ct_state ct_state_ram __attribute__((section(".mt_ram")));
#define CT (&ct_state_ram)

static void ct_reset(void)
{
    for (uint32_t t = 0; t < TRACKS; t++)
        CT->pass[t] = 0;
}

static void ct_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (CT->magic == CT_MAGIC)
        return;
    ct_reset();
    CT->pending = 0;
    CT->was_playing = 0;
    CT->magic = CT_MAGIC;
}

/* the audio interrupt, before reading or changing the counts */
static void ct_sync(void)
{
    ct_init_if_needed();
    if (*(volatile uint8_t *)&CT->pending) {
        CT->pending = 0;
        ct_reset();
    }
}

/* the main loop or the audio interrupt: playback starts from seq_play_from */
static void ct_request_reset(void)
{
    ct_init_if_needed();
    *(volatile uint8_t *)&CT->pending = 1;
}

/* A:B for the values from CT_RATIO: (A - 1) << 4 | B */
#define AB(a, b) (uint8_t)(((a) - 1) << 4 | (b))
static const uint8_t ct_ratio[CT_LAST - CT_RATIO + 1] = {
    AB(1, 2), AB(2, 2),
    AB(1, 3), AB(2, 3), AB(3, 3),
    AB(1, 4), AB(2, 4), AB(3, 4), AB(4, 4),
    AB(1, 5), AB(2, 5), AB(3, 5), AB(4, 5), AB(5, 5),
    AB(1, 6), AB(2, 6), AB(3, 6), AB(4, 6), AB(5, 6), AB(6, 6),
    AB(1, 7), AB(2, 7), AB(3, 7), AB(4, 7), AB(5, 7), AB(6, 7), AB(7, 7),
    AB(1, 8), AB(2, 8), AB(3, 8), AB(4, 8), AB(5, 8), AB(6, 8), AB(7, 8), AB(8, 8),
};

/* Does condition `v` (101..CT_LAST) hold on pass n? */
static int ct_holds(uint32_t v, uint32_t n)
{
    if (v == CT_FIRST)
        return n == 0;
    if (v == CT_NOT_FIRST)
        return n != 0;
    uint32_t ab = ct_ratio[v - CT_RATIO];
    return n % (ab & 0xFu) == ab >> 4;
}

/* ---- the Chance FX (audio interrupt) ------------------------------------------- */

/* ITCM 0x30ce: "bl rand_range_q(0, fx_max)" -> stub (r2 = r7, r3 = r8) -> here.
 * column = seq + 0x68 * track for FX2, 2 less for FX1: the FX's type is at +0x73 and
 * its value at +0x74. The step plays when the result is at most the value. */
int32_t mt_ct_chance(int32_t lo, int32_t hi, const uint8_t *column, uint32_t track)
{
    uint32_t v = column[0x74];
    if (v < CT_FIRST || v > CT_LAST || track >= TRACKS)
        return ((rand_fn)mt_ct_next_chance)(lo, hi, column, track);
    ct_sync();
    return ct_holds(v, CT->pass[track]) ? 0 : CT_FAIL;
}

/* ---- counting passes (audio interrupt) ------------------------------------------ */

/* A short track (tracklen) starts its own loop: its position is a nonzero multiple of
 * its length. */
static int ct_own_loop(const uint8_t *seq, const uint8_t *trk, uint32_t track)
{
    if (mt_ct_tl_len == 0u || (seq[SEQ_PERFORMANCE] && trk[TRK_PLAY_MODE] != 0u))
        return 0;
    uint32_t e = ((tl_len_fn)mt_ct_tl_len)(seq, track);
    uint32_t step = *(const volatile uint16_t *)(trk + TRK_STEP);
    return e != 0u && step != 0u && step % e == 0u;
}

/* the word the sequencer's ITCM code jumps through to seq_advance_step(seq, track),
 * 0x601acf00 in flash (chained): after a track ends a step */
uint32_t mt_ct_advance_step(void *seq, uint32_t track)
{
    uint8_t *s = (uint8_t *)seq;
    uint8_t *trk = s + SEQ_TRACK(track < TRACKS ? track : 0);
    uint32_t pattern = *FW_PROJECT_PATTERN;
    int32_t queued = *(volatile int32_t *)(trk + TRK_QUEUED);
    uint32_t r = ((advance_step_fn)mt_ct_next_advance_step)(seq, track);
    if (track >= TRACKS)
        return r;
    ct_sync();
    if (*(volatile int16_t *)(trk + TRK_LOOP_STEPS) == 0 || ct_own_loop(s, trk, track)) {
        uint32_t n = CT->pass[track] + 1u;      /* the track starts its loop again */
        CT->pass[track] = (uint16_t)(n >= 2u * CT_CYCLE ? n - CT_CYCLE : n);
    }
    if (queued >= 0 && *(volatile int32_t *)(trk + TRK_QUEUED) < 0)
        CT->pass[track] = 0;                /* its own queued pattern started */
    if (track == LAST_TRACK && *FW_PROJECT_PATTERN != pattern)
        ct_reset();                         /* another pattern plays */
    return r;
}

/* Before the sequencer runs, once per audio block: playback just started. */
static void ct_block(const uint8_t *seq)
{
    ct_sync();
    uint8_t playing = seq[SEQ_PLAYING] != 0;
    if (playing && !CT->was_playing)
        ct_reset();
    CT->was_playing = playing;
}

/* replaces "bl seq_advance" (veneer 0x600aa858) at 0x600aa7f8 in app_audio_block (chained) */
void mt_ct_seq_advance(void *seq, float seconds)
{
    ct_block((const uint8_t *)seq);
    ((advance_fn)mt_ct_next_seq_advance)(seq, seconds);
}

/* replaces "bl seq_advance" (veneer 0x600bc2d8) at 0x600bc27a in the render loop (chained) */
void mt_ct_render_advance(void *seq, float seconds)
{
    ct_block((const uint8_t *)seq);
    ((advance_fn)mt_ct_next_render_advance)(seq, seconds);
}

/* ---- playback starts ------------------------------------------------------------- */

/* ITCM 0x3752: "bl seq_play_from(seq, 0)" at the end of a count-in -> stub -> here */
void mt_ct_count_in(void *seq, uint32_t step)
{
    ct_request_reset();
    ((play_from_fn)mt_ct_next_count_in)(seq, step);
}

/* the literals of the four flash veneers to seq_play_from (chained) */
#define PLAY_FROM_HOOK(name, next) \
    void name(void *seq, uint32_t step) \
    { \
        ct_request_reset(); \
        ((play_from_fn)next)(seq, step); \
    }
PLAY_FROM_HOOK(mt_ct_play_from_a, mt_ct_next_play_from_a)
PLAY_FROM_HOOK(mt_ct_play_from_b, mt_ct_next_play_from_b)
PLAY_FROM_HOOK(mt_ct_play_from_c, mt_ct_next_play_from_c)
PLAY_FROM_HOOK(mt_ct_play_from_d, mt_ct_next_play_from_d)

/* ---- entering and showing conditions (main loop) --------------------------------- */

/* replaces "bl fx_max" (veneer 0x600af2c8) at 0x600af1b2 in seq_change_selection_fx_value
 * (chained): a cell's highest value for an encoder turn; the cell gets min(it, value) */
int32_t mt_ct_cell_max(void *selection, uint32_t type, int32_t value)
{
    if (type == FX_CHANCE)
        return CT_LAST;
    return ((max_fn)mt_ct_next_cell_max)(selection, type, value);
}

/* The 3-character name of condition v (101..CT_LAST). */
static void ct_name(uint32_t v, char *out)
{
    if (v == CT_FIRST) {
        out[0] = '1', out[1] = 's', out[2] = 't';
    } else if (v == CT_NOT_FIRST) {
        out[0] = '!', out[1] = '1', out[2] = 's';
    } else {
        uint32_t ab = ct_ratio[v - CT_RATIO];
        out[0] = (char)('1' + (ab >> 4)), out[1] = ':', out[2] = (char)('0' + (ab & 0xFu));
    }
    out[3] = 0;
}

/* A condition's name for fx_value_text_cell(seq, buf, size, column, track, step), or 0. */
static int ct_cell_text(const void *seq, char *buf, uint32_t size, uint32_t column, uint32_t track, int32_t step)
{
    int32_t s = (int8_t)step;               /* as fx_value_text_cell reads them: bytes, the step signed */
    uint32_t t = track & 0xFFu, c = column & 0xFFu;
    if (s < 0 || t >= TRACKS || c > 1u || size < 4u)
        return 0;
    const uint8_t *pattern = *(const uint8_t *const *)((const uint8_t *)seq + SEQ_PATTERN);
    const uint8_t *fx = pattern + PAT_FX(t, (uint32_t)s, c);
    if (fx[0] != FX_CHANCE || fx[1] < CT_FIRST || fx[1] > CT_LAST)
        return 0;
    ct_name(fx[1], buf);
    return 1;
}

/* replace "bl fx_value_text_cell" at 0x600df748 (FX1) and 0x600df77c (FX2) in the pattern
 * grid's row fill (chained); the grid ignores the result */
int32_t mt_ct_fx1_text(void *seq, char *buf, uint32_t size, uint32_t column, uint32_t track, int32_t step)
{
    if (ct_cell_text(seq, buf, size, column, track, step))
        return 3;
    return ((cell_text_fn)mt_ct_next_fx1_text)(seq, buf, size, column, track, step);
}

int32_t mt_ct_fx2_text(void *seq, char *buf, uint32_t size, uint32_t column, uint32_t track, int32_t step)
{
    if (ct_cell_text(seq, buf, size, column, track, step))
        return 3;
    return ((cell_text_fn)mt_ct_next_fx2_text)(seq, buf, size, column, track, step);
}

/* ---- version text: Config shows 1.2.0-<CONDTRIG_VERSION> ------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" CONDTRIG_VERSION;
