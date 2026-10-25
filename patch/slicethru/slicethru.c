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
 * The flags are saved per project in <project folder>/slicethru.bin (16 bytes: magic,
 * a 48-bit mask, a checksum), read and written from the Sample Playback and Pattern
 * screens' update methods (chained), one small read or write per call, only while the
 * file manager is idle:
 *  - another project: the flags are cleared at once (nothing of the old project plays
 *    through in the new one), then read from its file once the file manager is idle;
 *  - Save As (and a new project's first save, which is one): the firmware copies only
 *    its own files to the new folder, so the flags go with the project to its new name
 *    and are written there. The Save As request's name copy (0x60114eaa) marks it;
 *  - a new project ("New Project", no path) has no folder yet: nothing is written;
 *  - a flag whose instrument isn't an active Beat Slice instrument any more (another
 *    sample loaded into the slot, its mode changed elsewhere) is cleared;
 *  - a change is written a moment later.
 * Stock firmware never reads the file: there the instruments play Beat Slice.
 *
 * Shared hooks go through chain words (mt_bt_next_*) that tools/patch_ptf.py fills with
 * what the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef SLICETHRU_VERSION
#define SLICETHRU_VERSION "bt4"
#endif

/* ---- firmware 1.2.0 ------------------------------------------------------------------ */
#define FW_INSTRUMENTS      0x20200000u     /* 48 instruments, 0x174 bytes each */
#define FW_CURRENT_INSTR    0x202046C5u     /* the selected instrument */
#define INSTR_SIZE          0x174u
#define INSTR_COUNT         48u
#define INSTR_ACTIVE        0x00u           /* 1: the slot holds an instrument */
#define INSTR_PLAY_MODE     0x3Cu
#define MODE_BEAT_SLICE     5u
#define POINT_SAMPLE_END    0xFFFFu         /* points are 0..65535 of the sample */

#define FW_FM               0x2001E488u     /* file manager object; *FW_FM = FS object */
#define FM_STATE            0x13Du          /* file manager state; 0 or 0x0c when idle */
#define FM_SUBSTATE         0x13Fu
#define FW_PROJECT_PATH     ((const uint8_t *)(FW_FM + 0x241u))     /* 254 characters and a 0 */
#define FW_PROJECT_NAME     ((const uint8_t *)(FW_FM + 0x340u))     /* 32 characters and a 0 */
#define PATH_MAX_LEN        254u
#define NAME_MAX_LEN        32u
#define FW_SNPRINTF         0x6016712Du
#define FW_SHOW_MODE        0x600F7C55u     /* (screen): the Play Mode list and the mode's controls */

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
typedef void (*update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef void (*show_fn)(void *screen);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);
typedef char *(*strncpy_fn)(char *dst, const char *src, uint32_t n);

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
CHAIN_WORD(mt_bt_next_playback_update);     /* Sample Playback screen update 0x600f6071 */
CHAIN_WORD(mt_bt_next_pattern_update);      /* Pattern screen update 0x600e1f99 */
CHAIN_WORD(mt_bt_next_save_name);           /* strncpy 0x601673d7 (Save As: the new name) */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define BT_MAGIC            0x5442544Du     /* "MTBT" */
#define SAVE_DELAY          25u             /* screen updates between a change and the save */
#define LOAD_NONE           0u
#define LOAD                1u              /* read the file; none: no flags */

struct bt_state {
    uint32_t magic;
    uint32_t flags[2];                      /* bit i: instrument i plays through (i < 48) */
    volatile uint8_t cur;                   /* the Beat Slice note-on in progress plays through */
    uint8_t saved_valid;                    /* saved[] is what the file holds */
    uint8_t load;                           /* LOAD_*: the project's file is still to be read */
    uint8_t wait;                           /* updates left before saving */
    uint32_t session;                       /* project name + path the flags belong to */
    uint32_t saved[2];
    uint32_t save_as;                       /* the session a Save As moves the flags to; 0: none */
};
static struct bt_state bt_state_ram __attribute__((section(".mt_ram")));    /* the start of the patch's RAM */
#define BT (&bt_state_ram)

static void bt_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (BT->magic == BT_MAGIC)
        return;
    BT->flags[0] = BT->flags[1] = 0;
    BT->cur = 0;
    BT->saved_valid = 0;
    BT->wait = 0;
    BT->session = 0;
    BT->load = 0;
    BT->save_as = 0;
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

/* ---- slicethru.bin (main loop: the screens' update methods) ---------------------- */

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

#define FILE_MAGIC          0x31535442u     /* "BTS1" */
#define FILE_SIZE           16u             /* magic, the mask (8 bytes, bits 48..63 zero), checksum */
#define MASK_HIGH           0x0000FFFFu     /* flags[1]: instruments 32..47 */

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
    for (uint32_t i = 0; i < 8; i++)
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
    int n = ((snprintf_fn)FW_SNPRINTF)(path, size, "%s/%s/slicethru.bin",
                                       (const char *)FW_PROJECT_PATH, (const char *)FW_PROJECT_NAME);
    return n > 0 && (uint32_t)n < size;
}

static int file_manager_idle(void)
{
    const volatile uint8_t *fm = (const volatile uint8_t *)FW_FM;
    uint8_t state = fm[FM_STATE];
    return (state == 0 || state == 0x0C) && (fm[FM_SUBSTATE] & 0xFD) == 0;
}

/* The project's flags into BT->flags. 1: the file was read and applied. */
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
    ok = ok && get32(buf) == FILE_MAGIC && get32(buf + 12) == data_sum(buf + 4)
         && (get32(buf + 8) & ~MASK_HIGH) == 0;
    if (!ok)
        return 0;
    BT->flags[0] = get32(buf + 4);
    BT->flags[1] = get32(buf + 8);
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
    uint32_t lo = BT->flags[0], hi = BT->flags[1] & MASK_HIGH;
    put32(buf, FILE_MAGIC);
    put32(buf + 4, lo);
    put32(buf + 8, hi);
    put32(buf + 12, data_sum(buf + 4));
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    ((fs_remove_fn)vt[FS_REMOVE / 4])(fs, path);
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_WRITE, 0);
    if (!res.ok)
        return;
    int ok = ((fs_write_fn)vt[FS_WRITE / 4])(fs, buf, FILE_SIZE, res.handle) == (int)FILE_SIZE;
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    if (ok) {
        BT->saved[0] = lo;
        BT->saved[1] = hi;
        BT->saved_valid = 1;
    }
}

/* Clear the flags of instruments that aren't active Beat Slice instruments. */
static void clear_stale(void)
{
    for (uint32_t i = 0; i < INSTR_COUNT; i++) {
        if (!bt_flag(i))
            continue;
        const volatile uint8_t *ins = (const volatile uint8_t *)(FW_INSTRUMENTS + INSTR_SIZE * i);
        if (ins[INSTR_ACTIVE] != 1 || ins[INSTR_PLAY_MODE] != MODE_BEAT_SLICE)
            bt_set_flag(i, 0);
    }
}

/* At most one small file read or write per call. 1: the flags were (re)loaded. */
static int settings_tick(void)
{
    bt_init_if_needed();
    if (!*(void **)FW_FM)
        return 0;
    uint32_t session = project_session();
    int unsaved = project_unsaved();
    if (session != BT->session) {
        int save_as = session == BT->save_as;   /* the same project under its new name */
        BT->save_as = 0;
        BT->session = session;
        BT->saved_valid = 0;                /* after a Save As: written to the new folder */
        BT->wait = SAVE_DELAY;
        BT->load = unsaved || save_as ? LOAD_NONE : LOAD;
        if (!save_as)
            BT->flags[0] = BT->flags[1] = 0;     /* nothing of the old project plays through */
    }
    if (!file_manager_idle())
        return 0;
    if (BT->load != LOAD_NONE) {
        BT->load = LOAD_NONE;
        settings_load();                    /* none or a bad one: no flags */
        BT->saved[0] = BT->flags[0];        /* nothing to write until a flag changes */
        BT->saved[1] = BT->flags[1];
        BT->saved_valid = 1;
        return 1;
    }
    clear_stale();
    if (unsaved || (BT->saved_valid && BT->flags[0] == BT->saved[0]
                    && (BT->flags[1] & MASK_HIGH) == BT->saved[1])) {
        BT->wait = SAVE_DELAY;
        return 0;
    }
    if (BT->wait) {
        BT->wait--;
        return 0;
    }
    BT->wait = SAVE_DELAY;
    settings_save();
    return 0;
}

/* Sample Playback screen vtable slot 0x60042698 (update method 0x600f6071; chained). A
 * project's flags loaded while the screen shows a sample instrument redraw its list. */
void mt_bt_playback_update(void *screen, void *a1, void *a2, void *a3)
{
    int loaded = settings_tick();
    ((update_fn)mt_bt_next_playback_update)(screen, a1, a2, a3);
    uint32_t cur = *(volatile uint8_t *)FW_CURRENT_INSTR;
    if (loaded && cur < INSTR_COUNT && screen_instrument() == (int32_t)cur)
        ((show_fn)FW_SHOW_MODE)((void *)FW_SCREEN);
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99; chained) */
void mt_bt_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    ((update_fn)mt_bt_next_pattern_update)(screen, a1, a2, a3);
}

/* 0x60114eaa in the save request 0x60114e34: "bl strncpy(fm + 0x340, name, 0x21)",
 * run only for Save As, after the new path is copied. The next tick sees the new
 * session and keeps the flags for it. (Any screen: the request comes from the project
 * screen, whose update isn't hooked.) */
char *mt_bt_save_name(char *dst, const char *src, uint32_t n)
{
    char *r = ((strncpy_fn)mt_bt_next_save_name)(dst, src, n);
    bt_init_if_needed();
    BT->save_as = project_session();
    return r;
}

/* ---- version text: Config shows 1.2.0-<SLICETHRU_VERSION> ------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" SLICETHRU_VERSION;
