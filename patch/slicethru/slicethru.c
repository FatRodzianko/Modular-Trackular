/*
 * slicethru: Beat Slice (Play Through) for sample instruments (Polyend Tracker+
 * firmware 1.2.0). docs/BEAT_SLICE_THROUGH_PLAN.md has the design.
 *
 * A Beat Slice instrument (play mode 5) plays slice n - 24 for note n, from the slice's
 * start to the next slice's start (the sample end for the last slice). With its
 * play-through flag set, a note plays from the slice's start to the end of the sample.
 * The instrument stays a Beat Slice instrument: only the end point changes.
 *
 * Every Beat Slice note-on, from the sequencer, the pads or the restart after a fade,
 * goes through the play-mode dispatcher 0x60032abc, which calls the Beat Slice note-on
 * 0x60032b50(voice, instrument, note) at 0x60032b3a. That function turns the slice's
 * start and end points (0..65535 of the sample) into frames with 0x60100694, the end
 * at 0x60032c62, and computes the rest (frames to play, the reverse start, the stereo
 * partner's copy) from them. So:
 *  - mt_bt_bs_note_on (0x60032b3a) sets "cur" for the call: the instrument's flag;
 *  - mt_bt_end_frames (0x60032c62) turns the end point into 0xffff while cur is set.
 * cur is saved and restored around the call, so a note-on from the audio interrupt
 * that cuts into one from the main loop (pad preview) before stock masks the interrupt
 * keeps its own value and leaves the other's as it was.
 *
 * Phase 2: the flags live in this patch's RAM only; the Play Mode list entry and the
 * settings file come in phases 3 and 4.
 *
 * Shared hooks go through chain words (mt_bt_next_*) that tools/patch_ptf.py fills with
 * what the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef SLICETHRU_VERSION
#define SLICETHRU_VERSION "bt1"
#endif

/* ---- firmware 1.2.0 ------------------------------------------------------------------ */
#define FW_INSTRUMENTS      0x20200000u     /* 48 instruments, 0x174 bytes each */
#define INSTR_SIZE          0x174u
#define INSTR_COUNT         48u
#define INSTR_PLAY_MODE     0x3Cu
#define MODE_BEAT_SLICE     5u
#define POINT_SAMPLE_END    0xFFFFu         /* points are 0..65535 of the sample */

typedef void (*note_on_fn)(void *voice, uint32_t instrument, int32_t note);
typedef uint32_t (*to_frames_fn)(uint32_t point, uint32_t length, uint32_t address, uint32_t r3);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_bt_next_bs_note_on);          /* Beat Slice note-on 0x60032b51 */
CHAIN_WORD(mt_bt_next_end_frames);          /* point to frames 0x60100695 */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define BT_MAGIC            0x5442544Du     /* "MTBT" */

struct bt_state {
    uint32_t magic;
    uint32_t flags[2];                      /* bit i: instrument i plays through (i < 48) */
    volatile uint8_t cur;                   /* the Beat Slice note-on in progress plays through */
    uint8_t pad[3];
};
static struct bt_state bt_state_ram __attribute__((section(".mt_ram")));    /* the start of the patch's RAM */
#define BT (&bt_state_ram)

static void bt_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (BT->magic == BT_MAGIC)
        return;
    BT->flags[0] = BT->flags[1] = 0;
    BT->cur = 0;
    BT->magic = BT_MAGIC;
}

static uint32_t bt_flag(uint32_t i)
{
    return (BT->flags[i >> 5] >> (i & 31)) & 1u;
}

/* ---- the voice: once per Beat Slice note-on (audio interrupt or main loop) -------- */

/* 0x60032b3a in the play-mode dispatcher: "bl 0x60032b50(voice, instrument, note)",
 * the Beat Slice case */
void mt_bt_bs_note_on(void *voice, uint32_t instrument, int32_t note)
{
    bt_init_if_needed();
    uint32_t i = instrument < INSTR_COUNT ? instrument : INSTR_COUNT - 1;   /* as stock clamps */
    const volatile uint8_t *ins = (const volatile uint8_t *)(FW_INSTRUMENTS + INSTR_SIZE * i);
    uint8_t prev = BT->cur;
    BT->cur = (uint8_t)(bt_flag(i) && ins[INSTR_PLAY_MODE] == MODE_BEAT_SLICE);
    ((note_on_fn)mt_bt_next_bs_note_on)(voice, instrument, note);
    BT->cur = prev;
}

/* 0x60032c62 in the Beat Slice note-on: "bl 0x60100694(end point, length, address)" */
uint32_t mt_bt_end_frames(uint32_t point, uint32_t length, uint32_t address, uint32_t r3)
{
    if (BT->magic == BT_MAGIC && BT->cur)
        point = POINT_SAMPLE_END;
    return ((to_frames_fn)mt_bt_next_end_frames)(point, length, address, r3);
}

/* ---- version text: Config shows 1.2.0-<SLICETHRU_VERSION> ------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" SLICETHRU_VERSION;
