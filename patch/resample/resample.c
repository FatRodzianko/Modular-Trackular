/*
 * resample: live resampling in the Sample Recorder (Polyend Tracker+ firmware 1.2.0).
 * docs/LIVE_RESAMPLING_PLAN.md has the design (phases 1-6, rs4) and
 * docs/RESAMPLE_SOURCE_PLAN.md its phase 7 (rs5): two buttons in the setup, Tracker
 * Audio and Record Tracker.
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
 *    runs before that question and is unaffected. Once no recording runs, it also
 *    turns Record Tracker's tap off if the recorder's update didn't.
 *  - mt_rs_start (the recorder's start, vtable word 0x60042c58): a new visit starts
 *    with the gates shut and nothing armed, as the stock entry leaves the engine.
 *  - mt_rs_labels1-4 (bl 0x600fbc40 at four sites: the setup's slot 2-4 labels for every
 *    source but Radio, which leaves slots 2 and 3 empty): draws Tracker Audio in
 *    slot 2, and Record Tracker in slot 3 with Line In.
 *  - mt_rs_slot2_button / mt_rs_slot3_button (pool words 0x600f8e24 and 0x600f8e28 of
 *    the setup's button binder 0x600f8db4, which the recorder's binder 0x600f8e3c calls
 *    in the setup and which rebinds the slot buttons press-only; stock's slot 2 and 3
 *    handlers act there only for Radio): a press in the setup toggles the setting.
 *  - mt_rs_copy (bl 0x6010c7c8 at 0x6010c6f6, the record copy loop's copy of one input
 *    block): with Record Tracker on, adds the matching block of the master's record
 *    queues (E+0x1e58 / E+0x2248, which only Export uses otherwise).
 *
 * Tracker Audio off leaves Rec, Shift+Rec and Play as stock (the recording is still
 * moved). On: Play in the setup is heard, Shift+Rec and Play in the count-in start the
 * pattern with the recording, Play during a recording is heard.
 *
 * Each saved project keeps both settings in <project folder>/resample.bin (rs6), as swing
 * keeps swing.bin: read and written from the Pattern screen's update (mt_rs_pattern_update,
 * vtable word 0x600403dc) and the recorder's (outside a recording and its count-in), only
 * while the file manager is idle:
 *  - another project: both Off at once, then its file once the file manager is idle;
 *  - Save As (and a new project's first save, which is one): the firmware copies only its
 *    own files to the new folder, so the settings go with the project to its new name and
 *    are written there. The Save As request's name copy (bl at 0x60114eaa,
 *    mt_rs_save_name) marks it;
 *  - a new project ("New Project", no path) has no folder yet: both Off, nothing written.
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
#define RESAMPLE_VERSION "rs8"
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
#define FW_SCR_SOURCE       0x78u           /* 0 Line In, 1 Radio, 2 Mic LG, 3 Mic HG, 4 USB */
#define FW_SCR_MONITOR      0x7Eu           /* 0 = on: the input reaches the master */
#define FW_SCR_SLOT2        0x5Fu           /* the label controls' ids, slots 2 and 3 */
#define FW_SCR_SLOT3        0x60u
#define FW_SCR_POPUPS       (*(volatile const uint32_t *)(FW_SCR + 0xBBCu))   /* the slot handlers' pop-up tests */
#define SOURCE_LINE_IN      0u
#define SOURCE_RADIO        1u
#define FW_CONTROLS         0x20027D64u     /* the display's controls */
#define FW_CTL_TEXT         0x60101D5Cu     /* (controls, id, text): the label's first line */
#define FW_CTL_TEXT2        0x60101D74u     /* (controls, id, text): its second line (keeps the pointer) */
#define FW_CTL_REFRESH      0x60101E84u     /* (controls, id) */
#define FW_REC_CH_VECTOR    (*(volatile const uint32_t *)(FW_REC + 0x14u))  /* 8-byte entries, channel pointer first */
#define FW_REC_POS          (*(volatile const uint32_t *)(FW_REC + 0x30u))  /* frames recorded */
#define FW_REC_QUEUE_L      (*(volatile const uint32_t *)(FW_REC + 0x20u))  /* the input queues */
#define FW_REC_QUEUE_R      (*(volatile const uint32_t *)(FW_REC + 0x24u))
#define FW_MASTER_QUEUE_L   (FW_ENGINE + 0x1E58u)   /* the master mixer's record queues (Export's) */
#define FW_MASTER_QUEUE_R   (FW_ENGINE + 0x2248u)
#define FW_MASTER_L         (FW_ENGINE + 0x2508u)   /* the master mixers: input k's gain at +0x18 + 4k, */
#define FW_MASTER_R         (FW_ENGINE + 0x2574u)   /* the gain it ramps from at +0x34 + 4k (Q16) */
#define MASTER_PREVIEW      5u                      /* the preview player's input: the metronome's click too */
#define FW_QUEUE_ACTIVE     0x46u
#define FW_QUEUE_BEGIN      0x60036504u     /* (q): drop what it holds */
#define FW_QUEUE_AVAILABLE  0x600364ECu     /* (q): blocks held */
#define FW_QUEUE_READ       0x60036548u     /* (q): the next block's 128 samples */
#define FW_QUEUE_FREE       0x6003657Cu     /* (q): release the block read */
#define BLOCK_SAMPLES       128u
#define FW_FM               0x2001E488u     /* file manager object; *FW_FM = FS object */
#define FM_STATE            0x13Du          /* file manager state; 0 or 0x0c when idle */
#define FM_SUBSTATE         0x13Fu
#define FW_PROJECT_PATH     ((const uint8_t *)(FW_FM + 0x241u))     /* 254 characters and a 0 */
#define FW_PROJECT_NAME     ((const uint8_t *)(FW_FM + 0x340u))     /* 32 characters and a 0 */
#define PATH_MAX_LEN        254u
#define NAME_MAX_LEN        32u
#define FW_SNPRINTF         0x6016712Du
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
typedef void (*start_fn)(void *self, uint32_t arg);
typedef void (*labels_fn)(void *scr);
typedef void (*copy_fn)(void *dst, const void *src, uint32_t n);
typedef uint32_t (*q_fn)(uint32_t q);
typedef void (*ctl_text_fn)(uint32_t controls, uint32_t id, const char *text);
typedef void (*ctl_fn)(uint32_t controls, uint32_t id);
typedef void (*screen_update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef char *(*strncpy_fn)(char *dst, const char *src, uint32_t n);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);

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
CHAIN_WORD(mt_rs_next_start);               /* recorder start 0x600fa229 */
CHAIN_WORD(mt_rs_next_labels1);             /* the setup's slot 2-4 labels 0x600fbc41, one word per site */
CHAIN_WORD(mt_rs_next_labels2);
CHAIN_WORD(mt_rs_next_labels3);
CHAIN_WORD(mt_rs_next_labels4);
CHAIN_WORD(mt_rs_next_slot2_button);        /* 0x600f86ad */
CHAIN_WORD(mt_rs_next_slot3_button);        /* 0x600f8a4d */
CHAIN_WORD(mt_rs_next_copy);                /* the copy 0x6010c7c9 */
CHAIN_WORD(mt_rs_next_pattern_update);      /* Pattern screen update 0x600e1f99 */
CHAIN_WORD(mt_rs_next_save_name);           /* strncpy 0x601673d7 (Save As: the new name) */
CHAIN_WORD(mt_rs_next_project_update);      /* project screen update 0x600ead59 */
CHAIN_WORD(mt_rs_next_countin_done);        /* the count-in's end 0x600f8605 */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define RS_MAGIC            0x3853524Du     /* "MRS8" */
#define SAVE_DELAY          25u             /* screen updates between a change and the save */

struct rs_state {
    uint32_t magic;
    uint8_t moved;                          /* the recording in progress is in the free space */
    volatile uint8_t opened;                /* the patch opened the gates (read in the audio interrupt) */
    uint8_t armed;                          /* Shift+Rec (or Play in the count-in): start the pattern with the recording */
    uint8_t ran;                            /* a moved recording started: the update closes up after it */
    uint32_t buf;                           /* the last recording's buffer and size */
    uint32_t size;
    uint8_t audio;                          /* the user's settings: Tracker Audio, */
    uint8_t record;                         /* Record Tracker */
    volatile uint8_t mix;                   /* this recording takes the master's blocks too (audio interrupt) */
    volatile uint8_t muted;                 /* the preview's master input muted (click[] holds its gains) */
    uint8_t saved;                          /* what resample.bin holds: bit 0 audio, bit 1 record */
    uint8_t saved_valid;
    uint8_t load;                           /* the project's file is still to be read */
    uint8_t wait;                           /* updates left before saving */
    uint32_t session;                       /* project name + path the settings belong to */
    uint32_t save_as;                       /* the session a Save As moves the settings to; 0: none */
    uint32_t click[2];                      /* the master's preview gains (L, R), muted while the mix runs */
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
    RS->ran = 0;
    RS->buf = RS->size = 0;
    RS->audio = RS->record = 0;
    RS->mix = 0;
    RS->muted = 0;
    RS->saved = RS->saved_valid = RS->load = RS->wait = 0;
    RS->session = RS->save_as = 0;
    RS->click[0] = RS->click[1] = 0;
    RS->magic = RS_MAGIC;
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

/* ---- the record buffer (0x600f90d2) ------------------------------------------------ */

void mt_rs_rec_buffer(void *rec, uint32_t buf, uint32_t size, uint32_t ch, uint32_t ch2, uint32_t flag)
{
    rs_init_if_needed();
    RS->moved = 0;
    RS->ran = 0;
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
    if (RS->opened && !RS->moved)
        close_gates();                      /* stock's buffer: the engine goes off, as stock */
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
    if (shift && RS->audio)
        RS->armed = 1;
    else if (!FW_SCR_B(FW_SCR_ACTIVE))
        RS->armed = 0;
    uint32_t r = ((handler_fn)mt_rs_next_rec_button)(a, b, c, d);
    if (!FW_SCR_B(FW_SCR_ACTIVE))
        RS->armed = 0;                      /* the press started nothing (a pop-up, say) */
    return r;
}

/* The count-in's clicks play through the preview player (the sequencer's click sets its
 * master gain to the metronome volume, ITCM 0x42e4). The last click starts in the same
 * tick as the count-in's end, and the master's record queues update before the master
 * mixers, so a take's first master block is the one mixed before the record start. So
 * the preview's master input is muted from the count-in's end (mt_rs_countin_done, or
 * the record start without a count-in) until the mix ends: only the count-in and a
 * sample's preview use it, neither during a recording. */
static volatile uint32_t *master_gain(uint32_t mixer, uint32_t from)
{
    return (volatile uint32_t *)(mixer + (from ? 0x34u : 0x18u) + 4u * MASTER_PREVIEW);
}

static void preview_mute(void)              /* in the audio interrupt, or with it masked */
{
    if (RS->muted)
        return;
    RS->muted = 1;
    for (uint32_t k = 0; k < 2; k++) {
        uint32_t mixer = k ? FW_MASTER_R : FW_MASTER_L;
        RS->click[k] = *master_gain(mixer, 0);
        *master_gain(mixer, 0) = 0;
        *master_gain(mixer, 1) = 0;         /* at once, not ramped over the first block */
    }
}

static void preview_unmute(void)
{
    if (!RS->muted)
        return;
    RS->muted = 0;
    for (uint32_t k = 0; k < 2; k++) {
        uint32_t mixer = k ? FW_MASTER_R : FW_MASTER_L;
        if (*master_gain(mixer, 0) == 0)    /* unless stock set it meanwhile */
            *master_gain(mixer, 0) = RS->click[k];
    }
}

static void preview_unmute_masked(void)
{
    CALL0(FW_IRQ0_MASK);
    preview_unmute();
    CALL0(FW_IRQ0_UNMASK);
}

/* seq+0x6fc, set by the recorder's start (pool word 0x600fa490): called by the sequencer
 * at the count-in's end, in the audio interrupt, right after its last click started. */
void mt_rs_countin_done(void)
{
    ((void (*)(void))mt_rs_next_countin_done)();
    if (RS->magic == RS_MAGIC && RS->audio && RS->record && in_setup()
        && FW_SCR_B(FW_SCR_SOURCE) == SOURCE_LINE_IN)
        preview_mute();                     /* the mix will start: keep the last click out of it */
}

static void mix_start(void)                 /* the master's blocks from now on, with the input's */
{
    CALL0(FW_IRQ0_MASK);
    preview_mute();
    ((q_fn)(FW_QUEUE_BEGIN | 1u))(FW_REC_QUEUE_L);
    ((q_fn)(FW_QUEUE_BEGIN | 1u))(FW_REC_QUEUE_R);
    ((q_fn)(FW_QUEUE_BEGIN | 1u))(FW_MASTER_QUEUE_L);
    ((q_fn)(FW_QUEUE_BEGIN | 1u))(FW_MASTER_QUEUE_R);
    *(volatile uint8_t *)(FW_MASTER_QUEUE_L + FW_QUEUE_ACTIVE) = 1;
    *(volatile uint8_t *)(FW_MASTER_QUEUE_R + FW_QUEUE_ACTIVE) = 1;
    RS->mix = 1;
    CALL0(FW_IRQ0_UNMASK);
}

static void mix_end(void)                   /* in the audio interrupt, or with it masked */
{
    RS->mix = 0;
    preview_unmute();
    *(volatile uint8_t *)(FW_MASTER_QUEUE_L + FW_QUEUE_ACTIVE) = 0;
    *(volatile uint8_t *)(FW_MASTER_QUEUE_R + FW_QUEUE_ACTIVE) = 0;
    ((q_fn)(FW_QUEUE_BEGIN | 1u))(FW_MASTER_QUEUE_L);   /* hand back the blocks it holds */
    ((q_fn)(FW_QUEUE_BEGIN | 1u))(FW_MASTER_QUEUE_R);
}

static void mix_stop(void)
{
    CALL0(FW_IRQ0_MASK);
    mix_end();
    CALL0(FW_IRQ0_UNMASK);
}

void mt_rs_rec_started(void *rec)
{
    ((queues_start_fn)mt_rs_next_rec_started)(rec);
    if (RS->magic != RS_MAGIC)
        return;
    RS->ran = (uint8_t)moved_recording_on();
    if (RS->ran && RS->audio && RS->record && FW_SCR_B(FW_SCR_SOURCE) == SOURCE_LINE_IN)
        mix_start();
    else if (RS->muted)
        preview_unmute_masked();            /* muted at the count-in's end, but no mix */
    if (!RS->armed)
        return;
    RS->armed = 0;
    if (!RS->ran)
        return;                             /* stock's buffer: the engine stays off, as stock */
    if (pattern_playing())
        CALL1(FW_SEQ_STOP, FW_SEQ);         /* playing before the recording: play it from its start */
    if (!RS->opened)
        open_gates();
    CALL1(FW_SEQ_PLAY, FW_SEQ);
}

/* ---- Play (pool word 0x600fa4ac) ---------------------------------------------------- */

uint32_t mt_rs_play_button(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    if (RS->magic != RS_MAGIC || !RS->audio)
        return ((handler_fn)mt_rs_next_play_button)(a, b, c, d);
    int setup = in_setup();
    if (setup && FW_SCR_B(FW_SCR_ACTIVE)) {   /* counting in: as Shift+Rec */
        RS->armed = 1;
        return 0;
    }
    if ((setup || moved_recording_on()) && !RS->opened && !pattern_playing())
        open_gates();                       /* the press starts the pattern: let it be heard */
    return ((handler_fn)mt_rs_next_play_button)(a, b, c, d);
}

/* ---- the recorder's update (vtable word 0x60042c54) ------------------------------- */

static void draw_slots(void);
static int settings_tick(void);

uint32_t mt_rs_update(void *self)
{
    uint32_t r = ((update_fn)mt_rs_next_update)(self);
    if (RS->magic != RS_MAGIC)
        return r;
    if (RS->ran && !FW_REC_ON) {            /* the recording stopped */
        RS->ran = 0;
        if (RS->mix)
            mix_stop();
        if (RS->opened)
            close_gates();
    }
    if (RS->armed && !(FW_SCR_B(FW_SCR_ACTIVE) && in_setup()))
        RS->armed = 0;                      /* the count-in ended without a recording, or the screen changed */
    if (RS->muted && !RS->mix && !FW_SCR_B(FW_SCR_ACTIVE))
        preview_unmute_masked();            /* the count-in ended without a recording */
    if (!FW_REC_ON && !FW_SCR_B(FW_SCR_ACTIVE) && settings_tick())
        draw_slots();                       /* the settings changed with the project while the setup shows */
    return r;
}

/* ---- the recorder's start (vtable word 0x60042c58) -------------------------------- */

void mt_rs_start(void *self, uint32_t arg)
{
    rs_init_if_needed();
    if (RS->mix)
        mix_stop();
    if (RS->muted)
        preview_unmute_masked();
    RS->opened = 0;                         /* the stock start shuts the engine again */
    RS->armed = 0;
    RS->ran = 0;
    ((start_fn)mt_rs_next_start)(self, arg);
}

/* ---- the setup's labels (bl 0x600fbc40 at four sites) ----------------------------- */

static const char txt_audio[] = "Trk Audio";
static const char txt_record[] = "Trk Record";
static const char txt_on[] = "On";
static const char txt_off[] = "Off";
static const char txt_none[] = "";

static void draw_slot(uint32_t slot, const char *name, uint32_t on)
{
    uint32_t id = (uint32_t)(int32_t)(int8_t)FW_SCR_B(slot);
    ((ctl_text_fn)(FW_CTL_TEXT | 1u))(FW_CONTROLS, id, name);
    ((ctl_text_fn)(FW_CTL_TEXT2 | 1u))(FW_CONTROLS, id, name == txt_none ? txt_none : on ? txt_on : txt_off);
    ((ctl_fn)(FW_CTL_REFRESH | 1u))(FW_CONTROLS, id);
}

static void draw_slots(void)
{
    uint32_t source = FW_SCR_B(FW_SCR_SOURCE);
    if (FW_SCR_B(FW_SCR_MODE) != 0u || source == SOURCE_RADIO)
        return;
    draw_slot(FW_SCR_SLOT2, txt_audio, RS->audio);
    if (source == SOURCE_LINE_IN)
        draw_slot(FW_SCR_SLOT3, txt_record, RS->record);
    else
        draw_slot(FW_SCR_SLOT3, txt_none, 0);
}

static void labels(uint32_t next, void *scr)
{
    ((labels_fn)next)(scr);
    rs_init_if_needed();
    draw_slots();
}

void mt_rs_labels1(void *scr) { labels(mt_rs_next_labels1, scr); }
void mt_rs_labels2(void *scr) { labels(mt_rs_next_labels2, scr); }
void mt_rs_labels3(void *scr) { labels(mt_rs_next_labels3, scr); }
void mt_rs_labels4(void *scr) { labels(mt_rs_next_labels4, scr); }

/* ---- the slot 2 and 3 buttons in the setup (pool words 0x600f8e24, 0x600f8e28) ------ */
/* Bound press-only: the dispatcher calls them on a press, with nothing to read in r0-r3. */

static int slot_press(int line_in_only)     /* a press the patch takes */
{
    if (!in_setup() || FW_SCR_B(FW_SCR_ACTIVE))
        return 0;
    if ((FW_SCR_POPUPS & 0xFF0000FFu) != 0u || FW_SCR_B(0xBBAu) || FW_SCR_B(0xBBDu))
        return 0;
    uint32_t source = FW_SCR_B(FW_SCR_SOURCE);
    if (source == SOURCE_RADIO || (line_in_only && source != SOURCE_LINE_IN))
        return 0;
    rs_init_if_needed();
    return 1;
}

uint32_t mt_rs_slot2_button(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    if (!slot_press(0))
        return ((handler_fn)mt_rs_next_slot2_button)(a, b, c, d);
    RS->audio = !RS->audio;
    if (!RS->audio) {
        RS->record = 0;                     /* Record Tracker needs Tracker Audio */
        if (RS->opened)
            close_gates();                  /* stops the pattern Play started here */
    }
    draw_slots();
    return 0;
}

uint32_t mt_rs_slot3_button(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    if (!slot_press(1))
        return ((handler_fn)mt_rs_next_slot3_button)(a, b, c, d);
    RS->record = !RS->record;
    if (RS->record)
        RS->audio = 1;                      /* Record Tracker needs Tracker Audio */
    draw_slots();
    return 0;
}

/* ---- the record copy of one input block (bl at 0x6010c6f6, audio interrupt) ------- */

void mt_rs_copy(void *dst, const void *src, uint32_t n)
{
    ((copy_fn)mt_rs_next_copy)(dst, src, n);
    if (RS->magic != RS_MAGIC || !RS->mix || n != 2u * BLOCK_SAMPLES)
        return;
    uint32_t at = (uint32_t)dst - 2u * FW_REC_POS, q;
    if (at == *(volatile const uint32_t *)FW_REC_CH_VECTOR)
        q = FW_MASTER_QUEUE_L;
    else if (at == *(volatile const uint32_t *)(FW_REC_CH_VECTOR + 8u))
        q = FW_MASTER_QUEUE_R;
    else
        return;
    if (((q_fn)(FW_QUEUE_AVAILABLE | 1u))(q) == 0u)
        return;                             /* the master's first block comes one block later */
    const uint32_t *m = (const uint32_t *)((q_fn)(FW_QUEUE_READ | 1u))(q);
    uint32_t *out = (uint32_t *)dst;        /* both word aligned: two samples at a time */
    if (m && !(((uint32_t)m | (uint32_t)out) & 3u)) {
        if (FW_SCR_B(FW_SCR_MONITOR) == 0u) {
            for (uint32_t i = 0; i < BLOCK_SAMPLES / 2u; i++)
                out[i] = m[i];              /* Monitor on: the master holds Line In already */
        } else {
            for (uint32_t i = 0; i < BLOCK_SAMPLES / 2u; i++) {
                uint32_t v;
                __asm__("qadd16 %0, %1, %2" : "=r"(v) : "r"(out[i]), "r"(m[i]));   /* clipped to 16 bits */
                out[i] = v;
            }
        }
    }
    ((q_fn)(FW_QUEUE_FREE | 1u))(q);
}

/* ---- resample.bin ------------------------------------------------------------------ */

struct fs_open_result { uint32_t handle; uint8_t ok; uint8_t pad[3]; };
typedef void (*fs_open_fn)(struct fs_open_result *res, void *fs, const char *path, uint32_t mode, uint32_t x);
typedef int (*fs_read_fn)(void *fs, void *buf, uint32_t size, uint32_t handle);
typedef int (*fs_write_fn)(void *fs, const void *buf, uint32_t size, uint32_t handle);
typedef void (*fs_close_fn)(void *fs, uint32_t handle, uint32_t x);
typedef void (*fs_remove_fn)(void *fs, const char *path);
#define FS_REMOVE           0x14u
#define FS_OPEN             0x2Cu
#define FS_READ             0x30u
#define FS_WRITE            0x34u
#define FS_CLOSE            0x40u
#define FS_MODE_READ        0u
#define FS_MODE_WRITE       3u              /* FatFs create-always + RW, as the pattern save */

#define FILE_MAGIC          0x314D5352u     /* "RSM1" */
#define FILE_SIZE           12u             /* magic, settings, 3 zero bytes, 4-byte checksum */
#define SET_AUDIO           1u
#define SET_RECORD          2u

static uint32_t fnv1a(uint32_t h, uint32_t byte)
{
    return (h ^ byte) * 16777619u;
}

/* At most the lengths the firmware keeps: the Save As request copies the name before it
 * writes its terminating 0. */
static uint32_t project_session(void)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < NAME_MAX_LEN && FW_PROJECT_NAME[i]; i++)
        h = fnv1a(h, FW_PROJECT_NAME[i]);
    h = fnv1a(h, 0);
    for (uint32_t i = 0; i < PATH_MAX_LEN && FW_PROJECT_PATH[i]; i++)
        h = fnv1a(h, FW_PROJECT_PATH[i]);
    return fnv1a(h, 0) | 1;                 /* never 0 */
}

/* A project with no folder yet: no name (while Workspace loads), or the new project
 * 0x60117c8c makes, "New Project" with no path. */
static int project_unsaved(void)
{
    static const char fresh[] = "New Project";
    if (!FW_PROJECT_NAME[0])
        return 1;
    if (FW_PROJECT_PATH[0])
        return 0;
    for (uint32_t i = 0; i < sizeof fresh; i++)
        if (FW_PROJECT_NAME[i] != (uint8_t)fresh[i])
            return 0;
    return 1;
}

static uint32_t data_sum(const uint8_t *v)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < 4; i++)
        h = fnv1a(h, v[i]);
    return h;
}

static void put32(uint8_t *b, uint32_t v)
{
    b[0] = (uint8_t)v; b[1] = (uint8_t)(v >> 8); b[2] = (uint8_t)(v >> 16); b[3] = (uint8_t)(v >> 24);
}

static uint32_t get32(const uint8_t *b)
{
    return b[0] | b[1] << 8 | b[2] << 16 | (uint32_t)b[3] << 24;
}

static int settings_path(char *path, uint32_t size)
{
    if (project_unsaved())
        return 0;
    int n = ((snprintf_fn)FW_SNPRINTF)(path, size, "%s/%s/resample.bin",
                                       (const char *)FW_PROJECT_PATH, (const char *)FW_PROJECT_NAME);
    return n > 0 && (uint32_t)n < size;
}

static int file_manager_idle(void)
{
    const volatile uint8_t *fm = (const volatile uint8_t *)FW_FM;
    uint8_t state = fm[FM_STATE];
    return (state == 0 || state == 0x0C) && (fm[FM_SUBSTATE] & 0xFD) == 0;
}

static uint32_t settings_now(void)
{
    return (RS->audio ? SET_AUDIO : 0u) | (RS->record ? SET_RECORD : 0u);
}

static void settings_set(uint32_t v)        /* Record Tracker needs Tracker Audio */
{
    RS->record = (v & SET_RECORD) != 0u;
    RS->audio = (v & (SET_AUDIO | SET_RECORD)) != 0u;
}

static void settings_defaults(void)
{
    if (RS->opened && RS->audio)
        close_gates();                      /* (the gates open only inside the recorder) */
    settings_set(0);
}

/* 1: the file was read and applied */
static int settings_load(void)
{
    char path[256];
    void *fs = *(void **)FW_FM;
    if (!fs || !settings_path(path, sizeof path))
        return 0;
    void **vt = *(void ***)fs;
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    uint8_t buf[FILE_SIZE];
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_READ, 0);
    if (!res.ok)
        return 0;
    int ok = ((fs_read_fn)vt[FS_READ / 4])(fs, buf, FILE_SIZE, res.handle) == (int)FILE_SIZE;
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    ok = ok && get32(buf) == FILE_MAGIC && get32(buf + 8) == data_sum(buf + 4)
         && buf[4] <= (SET_AUDIO | SET_RECORD);
    if (!ok)
        return 0;
    settings_set(buf[4]);
    return 1;
}

static void settings_save(void)
{
    char path[256];
    void *fs = *(void **)FW_FM;
    if (!fs || !settings_path(path, sizeof path))
        return;
    void **vt = *(void ***)fs;
    uint8_t buf[FILE_SIZE];
    uint8_t v = (uint8_t)settings_now();
    put32(buf, FILE_MAGIC);
    buf[4] = v;
    buf[5] = buf[6] = buf[7] = 0;
    put32(buf + 8, data_sum(buf + 4));
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    ((fs_remove_fn)vt[FS_REMOVE / 4])(fs, path);
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_WRITE, 0);
    if (!res.ok)
        return;
    int ok = ((fs_write_fn)vt[FS_WRITE / 4])(fs, buf, FILE_SIZE, res.handle) == (int)FILE_SIZE;
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    if (ok) {
        RS->saved = v;
        RS->saved_valid = 1;
    }
}

/* At most one small file read or write per call. 1: the settings were reset or (re)loaded. */
static int settings_tick(void)
{
    rs_init_if_needed();
    if (!*(void **)FW_FM)
        return 0;
    int changed = 0;
    uint32_t session = project_session();
    int unsaved = project_unsaved();
    if (session != RS->session) {
        int save_as = session == RS->save_as;   /* the same project under its new name */
        RS->save_as = 0;
        RS->session = session;
        RS->saved_valid = 0;                /* after a Save As: written to the new folder */
        RS->wait = SAVE_DELAY;
        RS->load = (uint8_t)(!unsaved && !save_as);
        if (!save_as) {
            settings_defaults();            /* another project: its file, once read, decides */
            changed = 1;
        }
    }
    if (!file_manager_idle())
        return changed;
    if (RS->load) {
        RS->load = 0;
        settings_load();                    /* none or a bad one: both Off */
        RS->saved = (uint8_t)settings_now();
        RS->saved_valid = 1;                /* nothing to write until a setting changes */
        return 1;
    }
    if (unsaved || (RS->saved_valid && settings_now() == RS->saved)) {
        RS->wait = SAVE_DELAY;
        return changed;
    }
    if (RS->wait) {
        RS->wait--;
        return changed;
    }
    RS->wait = SAVE_DELAY;
    settings_save();
    return changed;
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99; chained) */
void mt_rs_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    ((screen_update_fn)mt_rs_next_pattern_update)(screen, a1, a2, a3);
}

/* Project screen vtable slot 0x6004193c (update method 0x600ead59): New Project, Open
 * and Save As happen there, so a new project is seen before its first save even when
 * neither the Pattern screen nor the recorder showed in between. */
uint32_t mt_rs_project_update(void *screen)
{
    settings_tick();
    return ((update_fn)mt_rs_next_project_update)(screen);
}

/* 0x60114eaa in the save request 0x60114e34: "bl strncpy(fm + 0x340, name, 0x21)",
 * run only for Save As, after the new path is copied. The next tick sees the new
 * session and keeps the settings for it. */
char *mt_rs_save_name(char *dst, const char *src, uint32_t n)
{
    char *r = ((strncpy_fn)mt_rs_next_save_name)(dst, src, n);
    rs_init_if_needed();
    RS->save_as = project_session();
    return r;
}

/* ---- "recording?" for engine_block_update (ITCM image word 0x601acc28) ------------ */

uint32_t mt_rs_is_recording(void *rec)
{
    if (RS->magic != RS_MAGIC)
        return ((is_recording_fn)mt_rs_next_is_recording)(rec);
    if (RS->mix && !FW_REC_ON)
        mix_end();                          /* stopped without the recorder's update (it left): the pool's blocks back */
    if (RS->opened && FW_REC_BUF == RS->buf)
        return 0;
    return ((is_recording_fn)mt_rs_next_is_recording)(rec);
}

/* ---- version text: Config shows 1.2.0-<RESAMPLE_VERSION> ------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" RESAMPLE_VERSION;
