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
 * The Play Mode list (Sample Playback screen) gets a ninth entry, "Beat Slice Thru",
 * after Beat Slice. Its row V maps to (mode, flag): 0..5 = mode V, flag off; 6 = Beat
 * Slice, flag on; 7, 8 = Wavetable, Granular. The stored mode stays 5, so every other
 * part of the firmware sees a Beat Slice instrument.
 *  - mt_bt_list_data (0x600f7cac): the list's data gets 9 names and the row V;
 *  - the encoder, +1 and -1 callbacks (words in the screen's callback pool) and the
 *    buttons 7/8 calls of the mode-change handler 0x600f5b98 (0x600f5e80, 0x600f5e8a):
 *    a step moves V, sets the flag, and calls the stock handler with the mode's step.
 *    A step that neither starts nor ends on row 6 goes to stock unchanged.
 *
 * Phase 3: the flags live in this patch's RAM only; the settings file comes in phase 4.
 *
 * Shared hooks go through chain words (mt_bt_next_*) that tools/patch_ptf.py fills with
 * what the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef SLICETHRU_VERSION
#define SLICETHRU_VERSION "bt2"
#endif

/* ---- firmware 1.2.0 ------------------------------------------------------------------ */
#define FW_INSTRUMENTS      0x20200000u     /* 48 instruments, 0x174 bytes each */
#define INSTR_SIZE          0x174u
#define INSTR_COUNT         48u
#define INSTR_PLAY_MODE     0x3Cu
#define MODE_BEAT_SLICE     5u
#define MODE_LAST           7u              /* Granular */
#define POINT_SAMPLE_END    0xFFFFu         /* points are 0..65535 of the sample */

#define FW_SCREEN           0x200254B4u     /* the Sample Playback screen object */
#define SCR_POPUP           0x70u           /* nonzero: the inputs go elsewhere */
#define SCR_FOCUS           0xD6u           /* the focused control; 6 = the Play Mode list */
#define SCR_INSTRUMENT      0xF4u           /* the instrument it shows */
#define FOCUS_PLAY_MODE     6u
#define FW_CHANGE_MODE      0x600F5B99u     /* (step): mode + step, clamped 0..7, and redraw */
#define FW_MODE_NAMES       0x60042618u     /* the 8 names, 15-byte slots */
#define MODE_NAME_SLOT      15u

/* the text list's data block (FINDINGS section 27) */
#define LIST_ROW            0x00u           /* i16 selected row */
#define LIST_COUNT          0x02u           /* u16 items */
#define LIST_NAMES          0x14u           /* char ** */

#define ROW_THRU            6               /* the new entry */
#define ROWS                9
#define STEP_MAX            4               /* stock clamps a step to -4..4 */

typedef void (*note_on_fn)(void *voice, uint32_t instrument, int32_t note);
typedef uint32_t (*to_frames_fn)(uint32_t point, uint32_t length, uint32_t address, uint32_t r3);
typedef void (*set_data_fn)(void *controls, uint32_t id, void *data);
typedef void (*callback_fn)(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3);
typedef void (*change_fn)(int32_t step);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_bt_next_bs_note_on);          /* Beat Slice note-on 0x60032b51 */
CHAIN_WORD(mt_bt_next_end_frames);          /* point to frames 0x60100695 */
CHAIN_WORD(mt_bt_next_list_data);           /* disp_ctrl_set_data 0x60101df9 */
CHAIN_WORD(mt_bt_next_encoder);             /* Sample Playback encoder callback 0x600c92e1 */
CHAIN_WORD(mt_bt_next_plus);                /* +1 callback 0x600c9d85 */
CHAIN_WORD(mt_bt_next_minus);               /* -1 callback 0x600ca7a5 */
CHAIN_WORD(mt_bt_next_button_plus);         /* mode-change handler 0x600f5b99 (8th button) */
CHAIN_WORD(mt_bt_next_button_minus);        /* mode-change handler 0x600f5b99 (7th button) */

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

static void bt_set_flag(uint32_t i, uint32_t on)
{
    uint32_t bit = 1u << (i & 31);
    if (on)
        BT->flags[i >> 5] |= bit;
    else
        BT->flags[i >> 5] &= ~bit;
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

/* ---- the Play Mode list (Sample Playback screen, main loop) ----------------------- */

static const char thru_name[] = "Beat Slice Thru";
static const char *const row_names[ROWS] = {
    (const char *)(FW_MODE_NAMES + 0 * MODE_NAME_SLOT),    /* 1-Shot */
    (const char *)(FW_MODE_NAMES + 1 * MODE_NAME_SLOT),    /* Forward Loop */
    (const char *)(FW_MODE_NAMES + 2 * MODE_NAME_SLOT),    /* Backward Loop */
    (const char *)(FW_MODE_NAMES + 3 * MODE_NAME_SLOT),    /* Pingpong Loop */
    (const char *)(FW_MODE_NAMES + 4 * MODE_NAME_SLOT),    /* Slice */
    (const char *)(FW_MODE_NAMES + 5 * MODE_NAME_SLOT),    /* Beat Slice */
    thru_name,
    (const char *)(FW_MODE_NAMES + 6 * MODE_NAME_SLOT),    /* Wavetable */
    (const char *)(FW_MODE_NAMES + 7 * MODE_NAME_SLOT),    /* Granular */
};

/* The instrument the screen shows, or -1. */
static int32_t screen_instrument(void)
{
    uint32_t p = *(volatile uint32_t *)(FW_SCREEN + SCR_INSTRUMENT);
    if (p < FW_INSTRUMENTS || p >= FW_INSTRUMENTS + INSTR_SIZE * INSTR_COUNT
        || (p - FW_INSTRUMENTS) % INSTR_SIZE)
        return -1;
    return (int32_t)((p - FW_INSTRUMENTS) / INSTR_SIZE);
}

static uint32_t mode_of(uint32_t i)
{
    return *(volatile uint8_t *)(FW_INSTRUMENTS + INSTR_SIZE * i + INSTR_PLAY_MODE);
}

/* The list row of instrument i: its mode, one further down from Wavetable on, or the
 * new entry for a Beat Slice instrument with its flag. */
static int32_t row_of(uint32_t i)
{
    uint32_t mode = mode_of(i);
    if (mode == MODE_BEAT_SLICE && bt_flag(i))
        return ROW_THRU;
    return mode > MODE_BEAT_SLICE ? (int32_t)mode + 1 : (int32_t)mode;
}

/* 0x600f7cac in 0x600f7c54: "bl disp_ctrl_set_data(controls, Play Mode list, data)" */
void mt_bt_list_data(void *controls, uint32_t id, void *data)
{
    bt_init_if_needed();
    int32_t i = screen_instrument();
    if (i >= 0 && data) {
        uint8_t *d = (uint8_t *)data;
        *(int16_t *)(d + LIST_ROW) = (int16_t)row_of((uint32_t)i);
        *(uint16_t *)(d + LIST_COUNT) = ROWS;
        *(const char *const **)(d + LIST_NAMES) = row_names;
    }
    ((set_data_fn)mt_bt_next_list_data)(controls, id, data);
}

/* One step of the list (main loop): change is the stock mode-change handler, called
 * with the step stock would use, or with the mode's step when row 6 is involved. */
static void list_step(int32_t step, uint32_t change)
{
    bt_init_if_needed();
    int32_t i = screen_instrument();
    if (i < 0) {
        ((change_fn)change)(step);
        return;
    }
    int32_t s = step > STEP_MAX ? STEP_MAX : step < -STEP_MAX ? -STEP_MAX : step;
    int32_t row = row_of((uint32_t)i);
    int32_t to = row + s;
    to = to < 0 ? 0 : to > ROWS - 1 ? ROWS - 1 : to;
    if (row != ROW_THRU && to != ROW_THRU && (row < ROW_THRU) == (to < ROW_THRU)) {
        bt_set_flag((uint32_t)i, 0);
        ((change_fn)change)(step);      /* stock's own step: the same mode as stock picks */
        return;
    }
    int32_t mode = (int32_t)mode_of((uint32_t)i);
    int32_t want = to == ROW_THRU ? (int32_t)MODE_BEAT_SLICE : to > ROW_THRU ? to - 1 : to;
    bt_set_flag((uint32_t)i, to == ROW_THRU);
    ((change_fn)change)(want - mode);   /* 0 between Beat Slice and Beat Slice Thru */
}

static int list_focused(void)
{
    const volatile uint8_t *scr = (const volatile uint8_t *)FW_SCREEN;
    return scr[SCR_POPUP] == 0 && scr[SCR_FOCUS] == FOCUS_PLAY_MODE;
}

/* Sample Playback screen callbacks (words 0x600f5178, 0x600f5144, 0x600f5140). With the
 * Play Mode list focused stock calls the handler with the encoder's step, +1 or -1. */
void mt_bt_encoder(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    if (list_focused()) {
        list_step((int32_t)r0, FW_CHANGE_MODE);
        return;
    }
    ((callback_fn)mt_bt_next_encoder)(r0, r1, r2, r3);
}

void mt_bt_plus(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    if (list_focused()) {
        list_step(1, FW_CHANGE_MODE);
        return;
    }
    ((callback_fn)mt_bt_next_plus)(r0, r1, r2, r3);
}

void mt_bt_minus(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    if (list_focused()) {
        list_step(-1, FW_CHANGE_MODE);
        return;
    }
    ((callback_fn)mt_bt_next_minus)(r0, r1, r2, r3);
}

/* 0x600f5e80 / 0x600f5e8a in the buttons 7/8 callback: "bl 0x600f5b98(+1 / -1)" */
void mt_bt_button_plus(int32_t step)
{
    list_step(step, mt_bt_next_button_plus);
}

void mt_bt_button_minus(int32_t step)
{
    list_step(step, mt_bt_next_button_minus);
}

/* ---- version text: Config shows 1.2.0-<SLICETHRU_VERSION> ------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" SLICETHRU_VERSION;
