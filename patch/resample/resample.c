/*
 * resample: live resampling in the Sample Recorder (Polyend Tracker+ firmware 1.2.0).
 * docs/LIVE_RESAMPLING_PLAN.md has the design; this is its phase 4 (rs4).
 *
 * Stock records into the start of the sample bank (0x8026a000), where the project's
 * samples are loaded, so a recording writes over them. That is why the recorder
 * blocks the sample instruments. Before the engine can play during a recording, the
 * recording has to go into the bank's free space instead.
 *
 * The record start 0x600f9074 calls 0x6010cb90(rec, buf, size, ch, ch, flag) at
 * 0x600f90d2 with buf = the bank's base and size = the whole bank. Every later reader
 * of the recording gets its channel pointers from the recorder object (0x6010ca68),
 * so moving buf and size there moves the whole recording.
 *  - mt_rs_rec_buffer (0x600f90d2): buf = base + the bank's bytes used (the loader
 *    puts each new sample there), rounded up; size = what is left, rounded down.
 *    With nothing loaded that is stock's buffer exactly. If less than MIN_SECONDS of
 *    recording would fit, the call goes to stock unchanged.
 *  - mt_rs_rec_button (the recorder's Rec handler, pool word 0x600f9000): a press that
 *    starts a count-in or a recording arms "start the pattern" if Shift is held. During
 *    the count-in, Shift+Rec arms and a plain Rec changes nothing.
 *  - mt_rs_rec_started (0x600f90d8, the record queues' start, reached from Rec and
 *    from the end of the count-in alike): if armed and the buffer was moved, open the
 *    gates and play the pattern from its start, so recording and pattern start together.
 *  - mt_rs_play_button (the recorder's Play handler, pool word 0x600fa4ac): during the
 *    count-in it arms instead (Play then acts like Shift+Rec; the stock handler would
 *    start the pattern silently over the count-in). While a moved recording runs and
 *    the press would start the pattern, open the gates first (the pattern starts in
 *    the next tick), then the stock handler.
 *  - mt_rs_update (the recorder's update, vtable word 0x60042c54): once the recording
 *    has stopped, stop the pattern and close the gates again, so the crop and save
 *    screens see the state the recorder's entry left. Disarm when nothing is counting
 *    in or recording.
 *  - mt_rs_is_recording (ITCM jump word 0x601acc28, the thunk at 0x1f540 that
 *    engine_block_update asks): "no" while the gates are open, so the per-block
 *    update runs in full (envelopes, LFO, automation, sends). The recording's copy
 *    runs before that question and is unaffected.
 *
 * "Open the gates" undoes the recorder entry's changes with the stock leave path's own
 * calls, except the line-in passthrough and the Monitor level, so the input is heard
 * only as Monitor says. "Close the gates" re-applies the entry's calls in its order.
 *
 * Hooks go through chain words (mt_rs_next_*) that tools/patch_ptf.py fills with what
 * the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef RESAMPLE_VERSION
#define RESAMPLE_VERSION "rs4"
#endif

/* ---- firmware 1.2.0 ------------------------------------------------------------------ */
#define FW_BANK_BASE        0x8026A000u     /* the sample bank (SDRAM) */
#define FW_BANK_SIZE        0x03C00000u
#define FW_BANK_TOTAL       (*(volatile const uint32_t *)0x20204F44u)  /* the bank's size */
#define FW_BANK_USED        (*(volatile const uint32_t *)0x20204F48u)  /* bytes the loaded samples take */

#define FW_REC              0x20027F28u     /* the recorder object */
#define FW_REC_BUF          (*(volatile const uint32_t *)(FW_REC + 0x00u))  /* its buffer */
#define FW_REC_ON           (*(volatile const uint8_t *)(FW_REC + 0x2Cu))   /* recording */
#define FW_SCR              0x20026100u     /* the Sample Recorder screen */
#define FW_SCR_B(off)       (*(volatile const uint8_t *)(FW_SCR + (off)))
#define FW_SCR_MODE         0x76u           /* 0 record setup, 1 crop/edit, 2 name/save */
#define FW_SCR_ACTIVE       0xB4Du          /* counting in or recording (set at both starts, cleared at the stop) */
#define FW_BUTTONS          (*(volatile const uint32_t *)0x200209E8u)
#define FW_BUTTON_HELD      0x600CF90Cu     /* (buttons, id): the Play handler's Shift test */
#define BUTTON_SHIFT        0x1Du
#define FW_ENGINE           0x20017B08u     /* the audio engine */
#define FW_SEQ              0x20017274u     /* the sequencer */
#define FW_SEQ_B(off)       (*(volatile const uint8_t *)(FW_SEQ + (off)))
#define FW_PLAYERS          0x2001A138u     /* the 8 sample players, 0x848 apart */
#define FW_PLAYER_SIZE      0x848u
#define FW_PLAYER_PENDING   0x798u          /* sends to re-apply */
#define FW_SYNTH_PENDING    0x1A48u         /* E + 4k: the 3 synths' sends to re-apply */

#define FW_SAMPLES_BLOCK    0x600B39CCu     /* (E): block the 48 sample instruments */
#define FW_SAMPLES_UNBLOCK  0x600B39E4u     /* (E) */
#define FW_SYNTH_GATE       0x600AD600u     /* (seq, on): seq+0x7dd, drop synth note-ons */
#define FW_DELAY_BYPASS     0x600B2DD8u     /* (E) */
#define FW_DELAY_UNBYPASS   0x600B2DE4u     /* (E) */
#define FW_DELAY_CLEAR      0x600B4058u     /* (E): delay send to 0 */
#define FW_REVERB_CLEAR     0x600B40C0u     /* (E): reverb send to 0 */
#define FW_MIX_SAVE_0DB     0x600B3A88u     /* (E): save the 11 mix levels, set them to 0 dB */
#define FW_MIX_RESTORE      0x600B3AE4u     /* (E) */
#define FW_LIMITER_RESET    0x600B36DCu     /* (E, 0): limiter side-chain mode and selectors */
#define FW_LIMITER_PROJECT  0x600B4128u     /* (E): the limiter from the project */
#define FW_SEQ_STOP         0x600ACD90u     /* (seq) */
#define FW_SEQ_PLAY         0x600AC5F8u     /* (seq): play the pattern from its start (the Play handler's) */
#define FW_IRQ0_MASK        0x600303D4u     /* the audio interrupt off */
#define FW_IRQ0_UNMASK      0x600303C8u     /* and on */

#define PENDING_PLAYER_SENDS 0x00100100u
#define PENDING_SYNTH_SENDS  0x6u           /* bit 1 delay, bit 2 reverb */

#define BUF_ALIGN           32u             /* the moved buffer's start */
#define SIZE_ALIGN          256u            /* its size: each channel's part stays 64-byte aligned */
#define RATE                44100u
#define MIN_SECONDS         10u             /* less free than this: stock's buffer */

typedef void (*rec_start_fn)(void *rec, uint32_t buf, uint32_t size, uint32_t ch,
                             uint32_t ch2, uint32_t flag);
typedef uint32_t (*handler_fn)(uint32_t a, uint32_t b, uint32_t c, uint32_t d);
typedef uint32_t (*update_fn)(void *self);
typedef uint32_t (*is_recording_fn)(void *rec);
typedef void (*queues_start_fn)(void *rec);
typedef uint32_t (*held_fn)(uint32_t buttons, uint32_t id);
typedef void (*fn1)(uint32_t a);
typedef void (*fn2)(uint32_t a, uint32_t b);
typedef void (*fn0)(void);

#define CALL0(f)        ((fn0)((f) | 1u))()
#define CALL1(f, a)     ((fn1)((f) | 1u))(a)
#define CALL2(f, a, b)  ((fn2)((f) | 1u))(a, b)

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_rs_next_rec_buffer);          /* recorder start 0x6010cb91 */
CHAIN_WORD(mt_rs_next_play_button);         /* Play handler 0x600d07b5 */
CHAIN_WORD(mt_rs_next_update);              /* recorder update 0x600fafe9 */
CHAIN_WORD(mt_rs_next_is_recording);        /* rec+0x2c 0x6010c889 */
CHAIN_WORD(mt_rs_next_rec_button);          /* Rec handler 0x600d0621 */
CHAIN_WORD(mt_rs_next_rec_started);         /* the record queues' start 0x6010c891 */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define RS_MAGIC            0x5352544Du     /* "MTRS" */

struct rs_state {
    uint32_t magic;
    uint8_t moved;                          /* the recording in progress is in the free space */
    volatile uint8_t opened;                /* the patch opened the gates (read in the audio interrupt) */
    uint8_t armed;                          /* Shift+Rec (or Play in the count-in): start the pattern with the recording */
    uint8_t pad;
    uint32_t buf;                           /* the last recording's buffer and size */
    uint32_t size;
};
static struct rs_state rs_state_ram __attribute__((section(".mt_ram")));    /* the start of the patch's RAM */
#define RS (&rs_state_ram)

static void rs_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (RS->magic == RS_MAGIC)
        return;
    RS->moved = 0;
    RS->opened = 0;
    RS->armed = 0;
    RS->buf = RS->size = 0;
    RS->magic = RS_MAGIC;
}

/* ---- the record buffer (0x600f90d2) ------------------------------------------------ */

void mt_rs_rec_buffer(void *rec, uint32_t buf, uint32_t size, uint32_t ch, uint32_t ch2, uint32_t flag)
{
    rs_init_if_needed();
    RS->moved = 0;
    RS->opened = 0;                         /* a new recording starts with the engine off, as stock */
    if (buf == FW_BANK_BASE && size == FW_BANK_SIZE && FW_BANK_TOTAL == FW_BANK_SIZE
        && (ch == 1u || ch == 2u)) {
        uint32_t used = FW_BANK_USED;
        if (used <= FW_BANK_SIZE - BUF_ALIGN) {
            uint32_t off = (used + BUF_ALIGN - 1u) & ~(BUF_ALIGN - 1u);
            uint32_t free = (FW_BANK_SIZE - off) & ~(SIZE_ALIGN - 1u);
            if (free >= MIN_SECONDS * RATE * 2u * ch) {
                buf += off;
                size = free;
                RS->moved = 1;
            }
        }
    }
    RS->buf = buf;
    RS->size = size;
    ((rec_start_fn)mt_rs_next_rec_buffer)(rec, buf, size, ch, ch2, flag);
}

/* ---- the gates --------------------------------------------------------------------- */

static int moved_recording_on(void)         /* a recording runs, in the buffer mt_rs_rec_buffer moved */
{
    return RS->magic == RS_MAGIC && RS->moved && FW_REC_ON && FW_REC_BUF == RS->buf;
}

static int pattern_playing(void)            /* the Play handler's own test (0x600d07b4) */
{
    return !FW_SEQ_B(0x2Du) && (FW_SEQ_B(0x1Du) || FW_SEQ_B(0x2Au));
}

static void open_gates(void)
{
    CALL1(FW_SAMPLES_UNBLOCK, FW_ENGINE);
    CALL2(FW_SYNTH_GATE, FW_SEQ, 0);
    CALL1(FW_DELAY_UNBYPASS, FW_ENGINE);
    CALL1(FW_MIX_RESTORE, FW_ENGINE);
    CALL1(FW_LIMITER_PROJECT, FW_ENGINE);
    CALL0(FW_IRQ0_MASK);                    /* the audio interrupt changes the same words */
    for (uint32_t t = 0; t < 8u; t++)
        *(volatile uint32_t *)(FW_PLAYERS + FW_PLAYER_SIZE * t + FW_PLAYER_PENDING) |= PENDING_PLAYER_SENDS;
    for (uint32_t k = 0; k < 3u; k++)
        *(volatile uint32_t *)(FW_ENGINE + FW_SYNTH_PENDING + 4u * k) |= PENDING_SYNTH_SENDS;
    RS->opened = 1;
    CALL0(FW_IRQ0_UNMASK);
}

static void close_gates(void)
{
    if (pattern_playing())
        CALL1(FW_SEQ_STOP, FW_SEQ);         /* the user's choice: the pattern stops with the recording */
    RS->opened = 0;
    CALL1(FW_SAMPLES_BLOCK, FW_ENGINE);     /* the recorder entry's calls, in its order */
    CALL2(FW_SYNTH_GATE, FW_SEQ, 1);
    CALL1(FW_DELAY_BYPASS, FW_ENGINE);
    CALL1(FW_DELAY_CLEAR, FW_ENGINE);
    CALL1(FW_REVERB_CLEAR, FW_ENGINE);
    CALL1(FW_MIX_SAVE_0DB, FW_ENGINE);
    CALL2(FW_LIMITER_RESET, FW_ENGINE, 0);
}

/* ---- Rec (pool word 0x600f9000) and the record start (0x600f90d8) ------------------ */

static int in_setup(void)                   /* record setup, no recording yet */
{
    return FW_SCR_B(FW_SCR_MODE) == 0u && !FW_REC_ON;
}

uint32_t mt_rs_rec_button(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    if (!in_setup())
        return ((handler_fn)mt_rs_next_rec_button)(a, b, c, d);
    rs_init_if_needed();
    /* Armed before the stock handler: without a count-in it records at once, and
     * mt_rs_rec_started runs inside it. A press during the count-in only arms, as
     * Play does; a plain Rec there leaves the choice made at its start. */
    int shift = ((held_fn)(FW_BUTTON_HELD | 1u))(FW_BUTTONS, BUTTON_SHIFT) != 0u;
    if (shift)
        RS->armed = 1;
    else if (!FW_SCR_B(FW_SCR_ACTIVE))
        RS->armed = 0;
    uint32_t r = ((handler_fn)mt_rs_next_rec_button)(a, b, c, d);
    if (!FW_SCR_B(FW_SCR_ACTIVE))
        RS->armed = 0;                      /* the press started nothing (a pop-up, say) */
    return r;
}

void mt_rs_rec_started(void *rec)
{
    ((queues_start_fn)mt_rs_next_rec_started)(rec);
    if (RS->magic != RS_MAGIC || !RS->armed)
        return;
    RS->armed = 0;
    if (!moved_recording_on())
        return;                             /* stock's buffer: the engine stays off, as stock */
    if (pattern_playing())
        CALL1(FW_SEQ_STOP, FW_SEQ);         /* started silently before the recording: play it from its start */
    open_gates();
    CALL1(FW_SEQ_PLAY, FW_SEQ);
}

/* ---- Play (pool word 0x600fa4ac) ---------------------------------------------------- */

uint32_t mt_rs_play_button(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    if (in_setup() && FW_SCR_B(FW_SCR_ACTIVE)) {   /* counting in: as Shift+Rec */
        rs_init_if_needed();
        RS->armed = 1;
        return 0;
    }
    if (moved_recording_on() && !RS->opened && !pattern_playing())
        open_gates();
    return ((handler_fn)mt_rs_next_play_button)(a, b, c, d);
}

/* ---- the recorder's update (vtable word 0x60042c54) ------------------------------- */

uint32_t mt_rs_update(void *self)
{
    uint32_t r = ((update_fn)mt_rs_next_update)(self);
    if (RS->magic != RS_MAGIC)
        return r;
    if (RS->opened && !FW_REC_ON)
        close_gates();
    if (RS->armed && !(FW_SCR_B(FW_SCR_ACTIVE) && in_setup()))
        RS->armed = 0;                      /* the count-in ended without a recording, or the screen changed */
    return r;
}

/* ---- "recording?" for engine_block_update (ITCM image word 0x601acc28) ------------ */

uint32_t mt_rs_is_recording(void *rec)
{
    if (RS->magic == RS_MAGIC && RS->opened && FW_REC_BUF == RS->buf)
        return 0;
    return ((is_recording_fn)mt_rs_next_is_recording)(rec);
}

/* ---- version text: Config shows 1.2.0-<RESAMPLE_VERSION> ------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" RESAMPLE_VERSION;
